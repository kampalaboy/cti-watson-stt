import pyaudio
from ibm_watson import SpeechToTextV1
from ibm_watson.websocket import RecognizeCallback, AudioSource
from threading import Thread, Event
from ibm_cloud_sdk_core.authenticators import IAMAuthenticator
import time
import uvicorn
import asyncio
import logging
import sys
import json  # Added for proper JSON handling

try:
    from Queue import Queue, Full, Empty  # Python 2
except ImportError:
    from queue import Queue, Full, Empty  # Python 3

from fastapi import FastAPI, HTTPException, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware

# --- Logging Configuration ---
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    stream=sys.stdout
)
logger = logging.getLogger("STT_APP")




# --- Configuration ---
IBM_API_KEY = '81F7RrschMIJRrtJXTEyWr2VRSz0MzKCMqm3vwWkaOJM'  # Your Watson API Key
# IMPORTANT: Set the correct service URL for your region
IBM_SERVICE_URL = 'https://api.us-south.speech-to-text.watson.cloud.ibm.com'

# Audio configuration
CHUNK_SIZE = 1024
PYAUDIO_FORMAT = pyaudio.paInt16
PYAUDIO_CHANNELS = 1
PYAUDIO_RATE = 44100

BUF_MAX_SIZE = CHUNK_SIZE * 50
AUDIO_QUEUE = Queue(maxsize=int(round(BUF_MAX_SIZE / CHUNK_SIZE)))

# --- Global Variables for STT State Management ---
PYAUDIO_INSTANCE = None
PYAUDIO_STREAM = None
RECOGNIZE_THREAD = None
AUDIO_SOURCE_INSTANCE = None

TRANSCRIPT_RESULTS = []
INTERIM_RESULTS = []
LAST_ERROR_MESSAGE = None
STT_ACTIVE = False
AUDIO_RECEIVED = False

# --- IBM Watson STT Initialization ---
try:
    logger.info(f"Initializing IBM Watson STT service...")
    authenticator = IAMAuthenticator(IBM_API_KEY)
    speech_to_text_service = SpeechToTextV1(authenticator=authenticator)

    if IBM_SERVICE_URL:
        speech_to_text_service.set_service_url(IBM_SERVICE_URL)
        logger.info(f"Set service URL to: {IBM_SERVICE_URL}")

    # Validate credentials
    try:
        models = speech_to_text_service.list_models().get_result()
        logger.info(
            f"Successfully connected to IBM Watson. Available models: {len(models['models'])}")
    except Exception as validation_error:
        logger.error(
            f"Could not validate Watson credentials: {validation_error}")
        # Continue anyway, might be a temporary issue
except Exception as e:
    logger.error(f"Failed to initialize IBM Watson STT service: {e}")
    LAST_ERROR_MESSAGE = f"IBM Watson STT initialization failed: {e}"

# --- FIX: Safe Data Logging Function ---


def safe_log_data(data, max_length=30):
    """Safely log data without causing slice errors"""
    if isinstance(data, dict):
        return json.dumps(data)[:max_length] + "..."
    elif isinstance(data, str):
        return data[:max_length] + "..." if len(data) > max_length else data
    elif hasattr(data, '__str__'):
        return str(data)[:max_length] + "..." if len(str(data)) > max_length else str(data)
    else:
        return "Non-loggable data type"

# --- Fixed Recognize Callback ---


class MyRecognizeCallback(RecognizeCallback):
    def __init__(self):
        RecognizeCallback.__init__(self)
        logger.info("Recognize callback initialized")

    def on_transcription(self, transcript):
        pass
        # global TRANSCRIPT_RESULTS, INTERIM_RESULTS
        # # FIX: Safer logging
        # logger.debug(
        #     f"Transcription callback received: {safe_log_data(transcript)}")

        # try:
        #     if transcript and isinstance(transcript, dict) and 'results' in transcript and len(transcript['results']) > 0:
        #         result = transcript['results'][0]
        #         # is_final = result.get('fiis_finalnal', False)

        #         if 'alternatives' in result and len(result['alternatives']) > 0:
        #             text = result['alternatives'][0]['transcript'].strip()
        #             confidence = result['alternatives'][0].get(
        #                 'confidence', 'N/A')

        #             if text:
        #                 logger.info(
        #                     f"Final transcript: '{text}' (Confidence: {confidence})")
        #                 TRANSCRIPT_RESULTS.append(text)
        #                 print(
        #                     f"TRANSCRIPT_RESULTS after append: {TRANSCRIPT_RESULTS}")
        #             else:
        #                 logger.debug(f"Interim transcript: '{text}'")
        #                 INTERIM_RESULTS.append(text)
        # except Exception as e:
        #     logger.error(f"Error processing transcript: {e}")

    def on_connected(self):
        logger.info('Watson STT WebSocket connection successful')

    def on_error(self, error):
        global LAST_ERROR_MESSAGE
        # FIX: Safer error handling
        try:
            error_str = f'Error from Watson STT: {str(error)}'
            logger.error(error_str)
            LAST_ERROR_MESSAGE = error_str
        except Exception as e:
            logger.error(f"Error while handling STT error: {e}")
            LAST_ERROR_MESSAGE = f"Error from Watson STT (details unavailable)"

    def on_inactivity_timeout(self, error):
        global LAST_ERROR_MESSAGE
        # FIX: Safer error handling
        try:
            error_str = f'Watson STT inactivity timeout: {str(error)}'
            logger.warning(error_str)
            LAST_ERROR_MESSAGE = error_str
        except Exception as e:
            logger.error(f"Error while handling inactivity timeout: {e}")
            LAST_ERROR_MESSAGE = "Watson STT inactivity timeout (details unavailable)"

    def on_listening(self):
        logger.info('Watson STT service is listening')

    def on_hypothesis(self, hypothesis):
        # FIX: Safer logging
        logger.debug(f"Watson STT hypothesis: {safe_log_data(hypothesis)}")

    def on_data(self, data):
        global TRANSCRIPT_RESULTS, INTERIM_RESULTS
        # FIX: Safer logging
        logger.debug(
            f"Transcription callback received: {safe_log_data(data)}")

        try:
            if data and isinstance(data, dict) and 'results' in data and len(data['results']) > 0:
                result = data['results'][0]
                is_final = result.get('final', False)

                if 'alternatives' in result and len(result['alternatives']) > 0:
                    text = result['alternatives'][0]['transcript'].strip()
                    print (text)
                    confidence = result['alternatives'][0].get(
                        'confidence', 'N/A')

                    if is_final:
                        
                            logger.info(
                                f"Final transcript: '{text}' (Confidence: {confidence})")
                            TRANSCRIPT_RESULTS.append(text)
                            print(
                                f"TRANSCRIPT_RESULTS after append: {TRANSCRIPT_RESULTS}")
                    else:
                        logger.debug(f"Interim transcript: '{text}'")
                        INTERIM_RESULTS.append(text)
        except Exception as e:
            logger.error(f"Error processing transcript: {e}")

        # FIX: Safer data handling
        logger.debug("Data received from Watson STT")

    def on_close(self):
        logger.info("Watson STT connection closed")

# --- Improved PyAudio Callback ---


def pyaudio_callback_for_stt(in_data, frame_count, time_info, status):
    global AUDIO_RECEIVED
    try:
        if STT_ACTIVE:
            # Check if audio contains actual data (not just zeros)
            # Some non-zero values above noise floor
            if max(bytearray(in_data[:100])) > 5:
                AUDIO_RECEIVED = True
                logger.debug(f"Audio data received: {len(in_data)} bytes")

            try:
                AUDIO_QUEUE.put_nowait(in_data)
            except Full:
                logger.warning("Audio queue is full, dropping audio frame")
    except Exception as e:
        logger.error(f"Error in PyAudio callback: {e}")
    return (None, pyaudio.paContinue)

# --- STT Control Functions ---


def test_microphone():
    """Test if microphone is working and can capture audio"""
    logger.info("Testing microphone...")
    p = pyaudio.PyAudio()
    try:
        # Get default input device info
        try:
            default_input = p.get_default_input_device_info()
            logger.info(
                f"Default input device: {default_input['name']} (index {default_input['index']})")
        except Exception as e:
            logger.warning(f"Could not get default input device: {e}")

        # Test recording a short sample
        stream = p.open(
            format=PYAUDIO_FORMAT,
            channels=PYAUDIO_CHANNELS,
            rate=PYAUDIO_RATE,
            input=True,
            frames_per_buffer=CHUNK_SIZE
        )

        logger.info("Recording 1 second test sample...")
        frames = []
        for _ in range(0, int(PYAUDIO_RATE / CHUNK_SIZE)):
            data = stream.read(CHUNK_SIZE, exception_on_overflow=False)
            # Check if audio contains actual data
            # Non-zero values above noise floor
            if max(bytearray(data[:100])) > 5:
                frames.append(data)

        stream.stop_stream()
        stream.close()

        if frames:
            logger.info(
                f"Microphone test successful! Captured {len(frames)} frames with data.")
            return True
        else:
            logger.warning("Microphone test warning: Only silence detected.")
            return False
    except Exception as e:
        logger.error(f"Microphone test failed: {e}")
        return False
    finally:
        p.terminate()


def initialize_stt_resources_if_needed():
    global PYAUDIO_INSTANCE, PYAUDIO_STREAM, AUDIO_SOURCE_INSTANCE, AUDIO_QUEUE, AUDIO_RECEIVED

    logger.info("Initializing STT resources...")

    # Reset audio received flag
    AUDIO_RECEIVED = False

    # Clear the queue
    while not AUDIO_QUEUE.empty():
        try:
            AUDIO_QUEUE.get_nowait()
        except Empty:
            break

    # Create PyAudio instance if needed
    if PYAUDIO_INSTANCE is None:
        PYAUDIO_INSTANCE = pyaudio.PyAudio()
        logger.info("PyAudio instance created")

    # Create audio source
    AUDIO_SOURCE_INSTANCE = AudioSource(AUDIO_QUEUE, True, True)
    logger.info("AudioSource instance created")

    # Close existing stream if present
    if PYAUDIO_STREAM is not None:
        if PYAUDIO_STREAM.is_active():
            PYAUDIO_STREAM.stop_stream()
        PYAUDIO_STREAM.close()
        PYAUDIO_STREAM = None
        logger.info("Closed existing PyAudio stream")

    # Create new stream
    try:
        PYAUDIO_STREAM = PYAUDIO_INSTANCE.open(
            format=PYAUDIO_FORMAT,
            channels=PYAUDIO_CHANNELS,
            rate=PYAUDIO_RATE,
            input=True,
            frames_per_buffer=CHUNK_SIZE,
            stream_callback=pyaudio_callback_for_stt,
            start=False
        )
        logger.info("PyAudio stream created successfully")
    except Exception as e:
        logger.error(f"Failed to create PyAudio stream: {e}")
        raise


def start_stt_session_logic():
    global PYAUDIO_STREAM, RECOGNIZE_THREAD, AUDIO_SOURCE_INSTANCE, STT_ACTIVE
    global TRANSCRIPT_RESULTS, INTERIM_RESULTS, LAST_ERROR_MESSAGE, AUDIO_RECEIVED

    if PYAUDIO_STREAM is None:
        logger.error("PyAudio stream not initialized")
        raise Exception("PyAudio stream not initialized")

    # Clear previous results
    TRANSCRIPT_RESULTS.clear()
    INTERIM_RESULTS.clear()
    LAST_ERROR_MESSAGE = None
    AUDIO_RECEIVED = False

    # Start PyAudio stream
    PYAUDIO_STREAM.start_stream()
    logger.info("PyAudio stream started")

    # Define recognition task
    def recognize_task():
        logger.info("Starting Watson STT recognition thread")
        my_callback = MyRecognizeCallback()
        try:
            # FIX: Use simpler parameters first to minimize errors
            logger.info(
                f"Audio format: audio/l16; rate={PYAUDIO_RATE}; channels={PYAUDIO_CHANNELS}")

            speech_to_text_service.recognize_using_websocket(
                audio=AUDIO_SOURCE_INSTANCE,
                content_type=f'audio/l16; rate={PYAUDIO_RATE}; channels={PYAUDIO_CHANNELS}',
                recognize_callback=my_callback,
                model='en-US_BroadbandModel',  # Standard model
                interim_results=True,
                inactivity_timeout=-1  # No timeout
            )
            logger.info(
                "Watson STT recognize_using_websocket completed normally")
        except Exception as e:
            global LAST_ERROR_MESSAGE
            error_str = f"Exception in Watson STT recognize thread: {str(e)}"
            logger.error(error_str)
            LAST_ERROR_MESSAGE = error_str

    # Start recognition thread
    RECOGNIZE_THREAD = Thread(target=recognize_task)
    RECOGNIZE_THREAD.daemon = True
    RECOGNIZE_THREAD.start()
    STT_ACTIVE = True
    logger.info("STT session active, recognition thread started")


def stop_stt_session_logic():
    time.sleep(3)
    global PYAUDIO_STREAM, PYAUDIO_INSTANCE, AUDIO_SOURCE_INSTANCE, RECOGNIZE_THREAD, STT_ACTIVE

    if not STT_ACTIVE and PYAUDIO_STREAM is None:
        logger.info("No active STT session to stop")
        return

    logger.info("Stopping STT session...")

    # Check audio reception
    if not AUDIO_RECEIVED:
        logger.warning("No audio data was received during this session!")

    # Stop audio stream first
    if PYAUDIO_STREAM is not None and PYAUDIO_STREAM.is_active():
        PYAUDIO_STREAM.stop_stream()
        logger.info("PyAudio stream stopped")

    # Signal that we're done recording
    if AUDIO_SOURCE_INSTANCE is not None:
        try:
            AUDIO_SOURCE_INSTANCE.completed_recording()
            logger.info("AudioSource.completed_recording() called")
        except Exception as e:
            logger.error(f"Error when signaling completed recording: {e}")

    # Wait for recognition thread to complete
    if RECOGNIZE_THREAD is not None and RECOGNIZE_THREAD.is_alive():
        logger.info("Waiting for Watson STT recognition thread to complete...")
        RECOGNIZE_THREAD.join(timeout=15.0)  # Increased timeout
        if RECOGNIZE_THREAD.is_alive():
            logger.warning("Watson STT thread did not complete in time")
        else:
            logger.info("Watson STT thread completed")

    # Close and clean up resources
    if PYAUDIO_STREAM is not None:
        try:
            PYAUDIO_STREAM.close()
            logger.info("PyAudio stream closed")
        except Exception as e:
            logger.error(f"Error closing PyAudio stream: {e}")
        PYAUDIO_STREAM = None

    if PYAUDIO_INSTANCE is not None:
        try:
            PYAUDIO_INSTANCE.terminate()
            logger.info("PyAudio instance terminated")
        except Exception as e:
            logger.error(f"Error terminating PyAudio instance: {e}")
        PYAUDIO_INSTANCE = None

    STT_ACTIVE = False
    logger.info("STT session stopped completely")


# --- FastAPI Application ---
app = FastAPI(title="IBM Watson STT API")
STT_OPERATION_LOCK = asyncio.Lock()

origins = ["*"]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
async def startup_event():
    logger.info("FastAPI application started")


@app.on_event("shutdown")
async def shutdown_event():
    logger.info("FastAPI application shutting down")
    if STT_ACTIVE:
        logger.info("Stopping active STT session during shutdown")
        await STT_OPERATION_LOCK.acquire()
        try:
            stop_stt_session_logic()
        finally:
            STT_OPERATION_LOCK.release()


@app.post("/test_microphone")
async def test_microphone_endpoint():
    """Test if microphone is working properly"""
    try:
        result = test_microphone()
        return {
            "success": result,
            "message": "Microphone test successful!" if result else "Microphone test warning: Only silence detected"
        }
    except Exception as e:
        logger.error(f"Microphone test endpoint error: {e}")
        raise HTTPException(
            status_code=500, detail=f"Microphone test failed: {str(e)}")


@app.post("/test_watson_credentials")
async def test_watson_credentials_endpoint():
    """Test if IBM Watson credentials are valid"""
    try:
        models = speech_to_text_service.list_models().get_result()
        return {
            "success": True,
            "message": f"Successfully connected to IBM Watson. Found {len(models['models'])} models.",
            "available_models": [model['name'] for model in models['models'][:5]]
        }
    except Exception as e:
        logger.error(f"Watson credentials test failed: {e}")
        raise HTTPException(
            status_code=500, detail=f"Watson credentials test failed: {str(e)}")


@app.post("/start_stt")
async def start_stt_endpoint(background_tasks: BackgroundTasks):
    global STT_ACTIVE

    async with STT_OPERATION_LOCK:
        if STT_ACTIVE:
            logger.warning(
                "Attempted to start STT when a session was already active")
            raise HTTPException(
                status_code=409, detail="An STT session is already active")

        try:
            logger.info("Request to start STT session received")

            # First, verify credentials before starting
            try:
                # Quick check that service is accessible
                speech_to_text_service.list_models()
                logger.info("Watson credentials verified")
            except Exception as e:
                logger.error(f"Watson credentials verification failed: {e}")
                raise HTTPException(status_code=500,
                                    detail=f"Cannot start STT: Watson credentials invalid or service unreachable. Error: {str(e)}")

            initialize_stt_resources_if_needed()
            start_stt_session_logic()

            # Add background task to stop session after a timeout
            background_tasks.add_task(
                auto_stop_after_timeout, 120)  # 2 minutes timeout

            return {
                "message": "STT session started successfully. Call /stop_stt to end and get transcript.",
                "instructions": "Speak clearly into your microphone. Session will automatically stop after 120 seconds if not stopped manually."
            }
        except Exception as e:
            logger.error(f"Error during STT start: {e}")
            # Cleanup if partial start
            try:
                stop_stt_session_logic()
            except:
                pass
            raise HTTPException(
                status_code=500, detail=f"Failed to start STT session: {str(e)}")


async def auto_stop_after_timeout(timeout_seconds: int):
    """Automatically stop STT session after timeout if still running"""
    global STT_ACTIVE

    logger.info(f"Auto-stop timer started for {timeout_seconds} seconds")
    await asyncio.sleep(timeout_seconds)

    if STT_ACTIVE:
        logger.warning(
            f"STT session timed out after {timeout_seconds} seconds, stopping automatically")
        async with STT_OPERATION_LOCK:
            if STT_ACTIVE:  # Check again after acquiring lock
                stop_stt_session_logic()


@app.post("/stop_stt")
async def stop_stt_endpoint():
    global STT_ACTIVE, TRANSCRIPT_RESULTS, INTERIM_RESULTS, LAST_ERROR_MESSAGE, AUDIO_RECEIVED

    async with STT_OPERATION_LOCK:
        # ADD THIS LINE
        print(
            f"[/stop_stt] TRANSCRIPT_RESULTS at the start: {TRANSCRIPT_RESULTS}")

        if not STT_ACTIVE and PYAUDIO_STREAM is None:
            logger.warning("Attempted to stop STT when no session was active")
            return {
                "message": "No active STT session to stop",
                "transcript": "",
                "error": None
            }

        logger.info("Request to stop STT session received")

        # Store results before stopping
        has_received_audio = AUDIO_RECEIVED

        # FIX: Make copies of the data before stopping to avoid race conditions
        transcript_copy = TRANSCRIPT_RESULTS.copy() if TRANSCRIPT_RESULTS else []
        interim_copy = INTERIM_RESULTS.copy() if INTERIM_RESULTS else []
        error_copy = LAST_ERROR_MESSAGE

        stop_stt_session_logic()

        # Process the copied data
        final_transcript = " ".join(transcript_copy).strip()

        response = {
            "message": "STT session stopped",
            "transcript": final_transcript if final_transcript else "No speech recognized or transcript available for this session",
            "error": error_copy,
            "debug_info": {
                "audio_received": has_received_audio,
                "interim_results_count": len(interim_copy),
                "last_interim_results": interim_copy[-5:] if interim_copy else [],
                "transcript_segments_count": len(transcript_copy)
            }
        }

        logger.info(
            f"STT session stopped. Transcript available: {'Yes' if final_transcript else 'No'}")
        return response


@app.get("/stt_status")
async def stt_status_endpoint():
    global STT_ACTIVE, TRANSCRIPT_RESULTS, INTERIM_RESULTS, LAST_ERROR_MESSAGE, AUDIO_RECEIVED

    try:
        status = {
            "stt_active": STT_ACTIVE,
            "audio_received": AUDIO_RECEIVED if STT_ACTIVE else False,
            "current_transcript_segments_count": len(TRANSCRIPT_RESULTS),
            "interim_results_count": len(INTERIM_RESULTS) if STT_ACTIVE else 0,
            "last_error": LAST_ERROR_MESSAGE
        }

        logger.debug(f"STT status requested")
        return status
    except Exception as e:
        logger.error(f"Error in status endpoint: {e}")
        return {
            "stt_active": STT_ACTIVE if 'STT_ACTIVE' in globals() else False,
            "error": f"Error retrieving complete status: {str(e)}"
        }

# --- New endpoint to check available language models ---


@app.get("/available_models")
async def available_models_endpoint():
    """Get list of all available speech recognition models"""
    try:
        models = speech_to_text_service.list_models().get_result()
        return {
            "count": len(models['models']),
            "models": [{"name": model['name'], "description": model.get('description', 'No description')}
                       for model in models['models']]
        }
    except Exception as e:
        logger.error(f"Error retrieving models: {e}")
        raise HTTPException(
            status_code=500, detail=f"Failed to retrieve models: {str(e)}")

# Run with:
# uvicorn fixed_stt_app:app --reload
if __name__ == '__main__':
    if 'uvicorn' not in sys.argv[0]:
        uvicorn.run("app:app", host='0.0.0.0', port=4050, reload=True)