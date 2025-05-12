# Use the official Selenium Standalone Chrome image as the base image
#FROM registry.access.redhat.com/ubi8/python-312:latest
FROM python:3.12-slim
#FROM python

# Set the working directory inside the container
WORKDIR /app


# Copy the requirements file to the container and install dependencies
COPY requirements.txt /app/requirements.txt
RUN apt-get update && apt-get install -y \
    gcc \
    portaudio19-dev \
    python3-dev \
    && rm -rf /var/lib/apt/lists/*


RUN pip3 install -r requirements.txt

# Copy your FastAPI Python script to the container
COPY . .

EXPOSE 4050

# Set the command to run your Python script
CMD ["python3", "app.py"]
