FROM python:3.7-slim-bullseye

# Install pip requirements.txt
COPY requirements.txt ./requirements.txt
RUN pip install -r requirements.txt

# Copies your code file from your action repository to the filesystem path `/` of the container
COPY app.py ./app.py

# Code file to execute when the docker container starts up (`entrypoint.sh`)
CMD ["python", "./app.py"]
