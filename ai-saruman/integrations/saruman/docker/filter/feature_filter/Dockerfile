FROM python:3.10.15-slim

WORKDIR /app

RUN apt-get update && apt-get install -y \
    build-essential \
    git \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
# Keep PyPI available for normal dependencies; the extra index supplies CPU Torch.
RUN python -m pip install --no-cache-dir pip==25.3 \
    && python -m pip install --no-cache-dir -r requirements.txt --extra-index-url https://download.pytorch.org/whl/cpu \
    && python -m pip check \
    && python -c "import torch; from transformers import BertForSequenceClassification, BertTokenizer; assert torch.version.cuda is None"

EXPOSE 22230

CMD ["uvicorn", "src.api:app", "--host", "0.0.0.0", "--port", "22230", "--reload"]

RUN mkdir -p data
