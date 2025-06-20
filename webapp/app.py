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

def crop_receipt_image(image_bytes):
    nparr = np.frombuffer(image_bytes, np.uint8)
    image = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
    orig = image.copy()
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    _, thresh = cv2.threshold(gray, 180, 255, cv2.THRESH_BINARY_INV)
    contours, _ = cv2.findContours(thresh, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        return image_bytes
    largest = max(contours, key=cv2.contourArea)
    x, y, w, h = cv2.boundingRect(largest)
    if w * h < 0.15 * image.shape[0] * image.shape[1]:
        return image_bytes
    cropped = orig[y:y+h, x:x+w]
    is_success, buffer = cv2.imencode(".jpg", cropped)
    return buffer.tobytes() if is_success else image_bytes

def rename_file(data, ext):
    def clean(s): return "".join([c for c in str(s) if c.isalnum() or c in "-_ "])
    
    date = data.get('Date', '') or ' '
    payee = data.get('Name', '') or ' '
    ref = data.get('Ref', '') or ' '
    project = data.get('Project', '') or ' '
    payment_method = data.get('Payment Method', '') or ''
    
    match = re.search(r"\*+(\d{4})", payment_method)
    last4 = match.group(1) if match else "XXXX"
    
    new_name = f"{date} CC{last4} {clean(payee)} Inv {clean(ref)} - {clean(project)}{ext}"
    return new_name

# ========== STREAMLIT APP UI ==========

st.title("Receipt Parser – Cropping + Renaming")
st.markdown("Upload receipts. We'll crop them, extract the relevant info, rename the file, and give you everything in a ZIP + Excel report.")

with st.form("upload_form"):
    uploaded_files = st.file_uploader("Upload receipts (images or PDFs)", type=["jpg", "jpeg", "png", "webp", "pdf"], accept_multiple_files=True)
    submitted = st.form_submit_button("Process Receipts")

if submitted and uploaded_files:
    with st.spinner("Processing receipts..."):
        out_dir = tempfile.mkdtemp()
        spreadsheet_rows = []
        zip_buf = io.BytesIO()
        with ZipFile(zip_buf, "w") as zipf:
            for uploaded_file in uploaded_files:
                ext = os.path.splitext(uploaded_file.name)[-1].lower()
                if ext == ".pdf":
                    pdf_bytes = uploaded_file.read()
                    images = convert_from_bytes(pdf_bytes, fmt="jpeg")
                    for idx, img in enumerate(images):
                        with tempfile.NamedTemporaryFile(delete=False, suffix=".jpg") as img_tmp:
                            img.save(img_tmp.name, format="JPEG")
                            img_tmp.seek(0)
                            with open(img_tmp.name, "rb") as imgf:
                                img_data = imgf.read()
                        receipt_data = parse_receipt_with_openai(img_data, RECEIPT_PROMPT)
                        if receipt_data:
                            spreadsheet_rows.append(receipt_data)
                            new_name = rename_file(receipt_data, ".jpg")
                            zipf.writestr(new_name, img_data)
                else:
                    img_bytes = uploaded_file.read()
                    cropped_bytes = crop_receipt_image(img_bytes)
                    receipt_data = parse_receipt_with_openai(cropped_bytes, RECEIPT_PROMPT)
                    if receipt_data:
                        spreadsheet_rows.append(receipt_data)
                        new_name = rename_file(receipt_data, ext)
                        zipf.writestr(new_name, cropped_bytes)
            df = pd.DataFrame(spreadsheet_rows)
            sheet_bytes = io.BytesIO()
            df.to_excel(sheet_bytes, index=False)
            zipf.writestr("Receipts Summary.xlsx", sheet_bytes.getvalue())
        zip_buf.seek(0)
        st.success("Done! Download your processed receipts below.")
        st.download_button("Download ZIP", zip_buf, file_name=f"Receipts_{datetime.now().strftime('%Y%m%d_%H%M%S')}.zip")
else:
    st.info("Please upload files to begin.")
