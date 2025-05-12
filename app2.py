from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.websockets import  WebSocketState
from ibm_watson import SpeechToTextV1
from ibm_watson.websocket import RecognizeCallback, AudioSource
from ibm_cloud_sdk_core.authenticators import IAMAuthenticator
from threading import Thread
from queue import Queue, Full
import logging
import asyncio
import json

# Configure logging
logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger("STT_APP")

# Initialize IBM Watson STT
IBM_API_KEY = '81F7RrschMIJRrtJXTEyWr2VRSz0MzKCMqm3vwWkaOJM'
IBM_SERVICE_URL = 'https://api.us-south.speech-to-text.watson.cloud.ibm.com'

authenticator = IAMAuthenticator(IBM_API_KEY)
speech_to_text = SpeechToTextV1(authenticator=authenticator)
speech_to_text.set_service_url(IBM_SERVICE_URL)

# Initialize FastAPI
app = FastAPI()

class MyRecognizeCallback(RecognizeCallback):
    def __init__(self, websocket: WebSocket, loop: asyncio.AbstractEventLoop):
        RecognizeCallback.__init__(self)
        self.websocket = websocket
        self.loop = loop
        self._closed = False
        logger.debug("Callback initialized")
    
    async def send_message(self, message: dict):
        try:
            if not self._closed:
                await self.websocket.send_json(message)
        except Exception as e:
            logger.error(f"Failed to send message: {e}")
            self._closed = True

    def _run_coroutine(self, coro):
        asyncio.run_coroutine_threadsafe(coro, self.loop)

    def on_transcription(self, transcript):
        self._run_coroutine(self.send_message({"type": "transcript", "data": transcript}))

    def on_connected(self):
        logger.info('STT Service connected')
        self._run_coroutine(self.send_message({"type": "connected"}))

    def on_error(self, error):
        self._run_coroutine(self.send_message({"type": "error", "data": str(error)}))

    def on_inactivity_timeout(self, error):
        self._run_coroutine(self.send_message({"type": "timeout", "data": str(error)}))

    def on_listening(self):
        logger.info('STT Service is now listening')
        self._run_coroutine(self.send_message({"type": "listening"}))

    def on_data(self, data):
        try:
            if data and isinstance(data, dict) and 'results' in data and len(data['results']) > 0:
                result = data['results'][0]
                is_final = result.get('final', False)

                if 'alternatives' in result and len(result['alternatives']) > 0:
                    text = result['alternatives'][0]['transcript'].strip()
                    confidence = result['alternatives'][0].get('confidence', 1.0)

                    message = {
                        "type": "final" if is_final else "interim",
                        "text": text,
                        "confidence": confidence
                    }
                    self._run_coroutine(self.send_message(message))
                    logger.debug(f"Sent {'final' if is_final else 'interim'} transcript: {text}")
            
        except Exception as e:
            logger.error(f"Error in on_data: {str(e)}", exc_info=True)
            self._run_coroutine(self.send_message({
                "type": "error",
                "error": str(e)
            }))

@app.websocket("/stt")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    logger.info("WebSocket connection established")
    
    loop = asyncio.get_event_loop()
    callback = MyRecognizeCallback(websocket, loop)
    connection_active = True
    
    # Initialize queue for audio streaming
    audio_queue = Queue(maxsize=100)
    audio_source = AudioSource(audio_queue, True, True)
    
    try:
        # Start Watson STT in background thread
        def recognize_task():
            try:
                speech_to_text.recognize_using_websocket(
                    audio=audio_source,
                    content_type='audio/webm;codecs=opus',
                    recognize_callback=callback,
                    model='en-US_BroadbandModel',
                    interim_results=True,
                    inactivity_timeout=-1
                )
            except Exception as e:
                logger.error(f"Recognition error: {e}")
                if not callback._closed:
                    loop.create_task(callback.send_message({
                        "type": "error",
                        "error": str(e)
                    }))

        recognize_thread = Thread(target=recognize_task)
        recognize_thread.daemon = True
        recognize_thread.start()

        # Handle incoming audio data
        while connection_active:
            try:
                message = await websocket.receive()
                
                if message["type"] == "websocket.disconnect":
                    break
                
                if message["type"] == "websocket.receive":
                    if "bytes" in message:
                        audio_data = message["bytes"]
                        try:
                            audio_queue.put_nowait(audio_data)
                        except Full:
                            logger.warning("Audio queue full, dropping frame")
                
            except WebSocketDisconnect:
                logger.info("Client disconnected")
                break
            except Exception as e:
                logger.error(f"Error processing message: {str(e)}")
                if connection_active:
                    await callback.send_message({
                        "type": "error",
                        "error": str(e)
                    })
                break
                
    except Exception as e:
        logger.error(f"WebSocket error: {str(e)}")
    finally:
        # Cleanup
        logger.info("Cleaning up resources")
        audio_source.completed_recording()
        callback._closed = True
        
        if recognize_thread.is_alive():
            recognize_thread.join(timeout=1.0)
            
        if not websocket.client_state == WebSocketState.DISCONNECTED:
            await websocket.close()

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=4050)