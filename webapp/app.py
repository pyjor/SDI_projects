import streamlit as st
import openai
import os
import cv2
import json
import re
import pandas as pd
import numpy as np
import base64
from PIL import Image
from datetime import datetime
from zipfile import ZipFile
from pdf2image import convert_from_bytes
import tempfile
import io

# ======= CONFIG =======
openai.api_key = st.secrets["OPENAI_API_KEY"]

PROMPT_FILE = "webapp/receipt_prompt.txt"
with open(PROMPT_FILE, "r", encoding="utf-8") as f:
    RECEIPT_PROMPT = f.read()

# ========= FUNCTIONS ==========

def extract_json_from_response(text_response):
    match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text_response, re.DOTALL)
    if match:
        json_str = match.group(1)
    else:
        json_str = text_response.strip()
    return json_str

def parse_receipt_with_openai(image_bytes, prompt):
    base64_image = base64.b64encode(image_bytes).decode("utf-8")
    response = openai.chat.completions.create(
        model="gpt-4.1-mini",
        messages=[
            {"role": "system", "content": prompt},
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "Extract the receipt info as described."},
                    {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{base64_image}"}}
                ]
            }
        ],
        max_tokens=512
    )
    text_response = response.choices[0].message.content
    try:
        json_str = extract_json_from_response(text_response)
        data = json.loads(json_str)
    except Exception as e:
        st.warning(f"Error parsing JSON: {e}\nRaw: {text_response}")
        data = None
    return data

def rename_file(data, ext):
    def clean(s): return "".join([c for c in str(s) if c.isalnum() or c in "-_ "])

