FROM cgr.dev/chainguard/python:latest-dev as builder
WORKDIR /helm_image_updater
COPY requirements.txt .
RUN pip install -r requirements.txt --user --no-cache-dir
FROM cgr.dev/chainguard/python:latest
WORKDIR /helm_image_updater
COPY --from=builder /home/nonroot/.local/lib/python3.13/site-packages /home/nonroot/.local/lib/python3.13/site-packages
COPY cli.py .
ENTRYPOINT [ "python", "/helm_image_updater/cli.py" ]
