FROM docker.io/library/python:3
COPY "requirements.txt" "/usr/src/app/"
RUN pip install --no-cache-dir -r /usr/src/app/requirements.txt
COPY "main.py" "/usr/src/app/"
ENTRYPOINT ["python", "/usr/src/app/main.py"]
