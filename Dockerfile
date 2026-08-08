# Python Image
FROM python:3.10-slim

# System Level packages (unrar / p7zip) ইনস্টল
RUN apt-get update && apt-get install -y \
    p7zip-full \
    unrar-free \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Requirements ইনস্টল
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# সব ফাইল কপি
COPY . .

EXPOSE 10000

# অ্যাপ রান করার কমান্ড (আপনার পাইথন ফাইলের নাম main.py না হলে সেটি দিন)
CMD ["python", "main.py"]
