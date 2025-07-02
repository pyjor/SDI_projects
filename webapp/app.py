# SDI Services Combined App
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
import matplotlib.pyplot as plt
import seaborn as sns

# Streamlit page config
st.set_page_config(page_title="SDI Services", layout="wide")

# Centered layout with custom button grid
st.markdown("""
    <h1 style='text-align: center; color: white;'>SDI SERVICES</h1>
    <style>
        div.button-container {
            display: flex;
            flex-direction: row;
            justify-content: center;
            gap: 50px;
            flex-wrap: wrap;
            margin-top: 30px;
        }
        div.button-column {
            display: flex;
            flex-direction: column;
            gap: 20px;
        }
        button[kind="primary"] {
            border-radius: 30px !important;
            padding: 10px 30px !important;
        }
    </style>
    <div class='button-container'>
        <div class='button-column'>
            <form action="/?app=app1" method="get"><button kind="primary">APP 1</button></form>
            <form action="/?app=app3" method="get"><button kind="primary">APP 3</button></form>
            <form action="/?app=app5" method="get"><button kind="primary">APP 5</button></form>
        </div>
        <div class='button-column'>
            <form action="/?app=app2" method="get"><button kind="primary">APP 2</button></form>
            <form action="/?app=app4" method="get"><button kind="primary">APP 4</button></form>
            <form action="/?app=app6" method="get"><button kind="primary">APP 6</button></form>
        </div>
    </div>
""", unsafe_allow_html=True)

query_params = st.experimental_get_query_params()
selected_app = query_params.get("app", [None])[0]

# Load prompt file only once
PROMPT_FILE = "webapp/receipt_prompt.txt"
with open(PROMPT_FILE, "r", encoding="utf-8") as f:
    RECEIPT_PROMPT = f.read()

openai.api_key = st.secrets["OPENAI_API_KEY"]

# =================== APP 1 ===================
def app1():
    st.title("SDI Receipt Reader")
    st.markdown("Upload receipts. We'll extract the relevant info, rename the file, and give you everything in a ZIP + Excel report.")

    def extract_json_from_response(text_response):
        match = re.search(r"```(?:json)?\\s*(\{.*?\})\\s*```", text_response, re.DOTALL)
        json_str = match.group(1) if match else text_response.strip()
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
        date = data.get('Date', '') or ' '
        payee = data.get('Name', '') or ' '
        ref = data.get('Ref', '') or ' '
        project = data.get('Project', '') or ' '
        payment_method = data.get('Payment Method', '') or ''
        match = re.search(r"\\*+(\\d{4})", payment_method)
        last4 = match.group(1) if match else "XXXX"
        return f"{date} CC{last4} {clean(payee)} Inv {clean(ref)} - {clean(project)}{ext}"

    with st.form("upload_form_app1"):
        uploaded_files = st.file_uploader("Upload receipts (images or PDFs)", type=["jpg", "jpeg", "png", "webp"], accept_multiple_files=True, key="app1_uploader")
        submitted = st.form_submit_button("Process Receipts")

    if submitted and uploaded_files:
        with st.spinner("Processing receipts..."):
            out_dir = tempfile.mkdtemp()
            spreadsheet_rows = []
            zip_buf = io.BytesIO()
            with ZipFile(zip_buf, "w") as zipf:
                for uploaded_file in uploaded_files:
                    ext = os.path.splitext(uploaded_file.name)[-1].lower()
                    img_bytes = uploaded_file.read()
                    receipt_data = parse_receipt_with_openai(img_bytes, RECEIPT_PROMPT)
                    if receipt_data:
                        spreadsheet_rows.append(receipt_data)
                        new_name = rename_file(receipt_data, ext)
                        zipf.writestr(new_name, img_bytes)
                df_receipts = pd.DataFrame(spreadsheet_rows)
                sheet_bytes = io.BytesIO()
                df_receipts.to_excel(sheet_bytes, index=False)
                zipf.writestr("Receipts Summary.xlsx", sheet_bytes.getvalue())
            zip_buf.seek(0)
            st.success("Done! Download your processed receipts below.")
            st.download_button("Download ZIP", zip_buf, file_name=f"Receipts_{datetime.now().strftime('%Y%m%d_%H%M%S')}.zip")

# =================== APP 2 ===================
def app2():
    st.title("QuickBooks PnL Project Extractor")
    st.subheader("All Excel Files MUST be in Numeric Format")

    def extract_with_month_from_b6(file_obj, file_name):
        df_excel = pd.read_excel(file_obj, sheet_name=0, header=None)
        project_names = df_excel.iloc[4, 1:].fillna("").astype(str).str.strip()
        valid_cols = [i for i, name in enumerate(project_names, start=1) if name and "total" not in name.lower() and "(" in name and ")" in name]
        month_cell = df_excel.iloc[5, 1]
        month = str(month_cell).strip() if pd.notna(month_cell) else file_name
        sales_row_index = df_excel[df_excel.iloc[:, 0].astype(str).str.strip() == "61100 Contract Sales"].index
        cogs_row_index = df_excel[df_excel.iloc[:, 0].astype(str).str.strip() == "Total Cost of Goods Sold"].index
        if not sales_row_index.empty and not cogs_row_index.empty:
            sales_row = df_excel.iloc[sales_row_index[0], valid_cols].fillna(0).astype(float)
            cogs_row = df_excel.iloc[cogs_row_index[0], valid_cols].fillna(0).astype(float)
            sales_df = pd.DataFrame({'Project': [project_names[i] + " - Income" for i in valid_cols], month: sales_row.values})
            cogs_df = pd.DataFrame({'Project': [project_names[i] + " - Cost" for i in valid_cols], month: cogs_row.values})
            return pd.concat([sales_df, cogs_df], axis=0).reset_index(drop=True)
        return pd.DataFrame()

    uploaded_files2 = st.file_uploader("Upload one or more Excel files", type=["xlsx"], accept_multiple_files=True, key="app2_uploader")

    if uploaded_files2:
        all_data = []
        for file in uploaded_files2:
            result = extract_with_month_from_b6(file, file.name)
            all_data.append(result)

        combined_df = pd.concat(all_data, axis=0)
        pivot_df = combined_df.pivot_table(index="Project", aggfunc='first').fillna(0)

        profit_dict = {}
        projects = set(idx.replace(" - Income", "").replace(" - Cost", "") for idx in pivot_df.index)
        for project in projects:
            income_key = project + " - Income"
            cost_key = project + " - Cost"
            income_row = pivot_df.loc[income_key] if income_key in pivot_df.index else pd.Series(0, index=pivot_df.columns)
            cost_row = pivot_df.loc[cost_key] if cost_key in pivot_df.index else pd.Series(0, index=pivot_df.columns)
            profit_dict[project] = income_row - cost_row

        profit_df = pd.DataFrame.from_dict(profit_dict, orient='index').fillna(0)
        profit_df.index.name = "Project"
        pivot_df.loc["Total"] = pivot_df.sum()
        profit_df.loc["Total"] = profit_df.sum()

        st.success("AWESOME, Data processed successfully!")
        st.subheader("📌 Project Summary Table")
        st.dataframe(pivot_df.style.format("${:,.2f}"))

        st.subheader("📌 Profit and Loss Table")
        styled_profit_df = profit_df.style.format("${:,.2f}").applymap(lambda val: "background-color: #ffe6e6" if val < 0 else "")
        st.dataframe(styled_profit_df)

        with st.expander("📈 Show Project Charts"):
            selected_project = st.selectbox("Choose a project:", sorted(projects))
            if st.button("Generate Charts"):
                income_key = selected_project + " - Income"
                cost_key = selected_project + " - Cost"
                income_series = pivot_df.loc[income_key] if income_key in pivot_df.index else pd.Series(0, index=pivot_df.columns)
                cost_series = pivot_df.loc[cost_key] if cost_key in pivot_df.index else pd.Series(0, index=pivot_df.columns)
                df_plot = pd.DataFrame({
                    'Month': pivot_df.columns,
                    'Income': income_series.values,
                    'Cost': cost_series.values,
                    'Net Profit': income_series.values - cost_series.values,
                    'Margin %': ((income_series.values - cost_series.values) / income_series.replace(0, float('nan')).values) * 100
                })
                st.line_chart(df_plot.set_index('Month')[['Income', 'Cost']])
                st.bar_chart(df_plot.set_index('Month')['Net Profit'])
                st.line_chart(df_plot.set_index('Month')['Margin %'])

        buffer = io.BytesIO()
        with pd.ExcelWriter(buffer, engine='openpyxl') as writer:
            pivot_df.to_excel(writer, index=True, sheet_name='Project Summary')
            profit_df.to_excel(writer, index=True, sheet_name='Profit and Loss')

        st.download_button("📥 Download Excel File", data=buffer.getvalue(), file_name="project_summary.xlsx", mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    else:
        st.info("Please upload at least one Excel file to begin.")

# ========== Show App Based on Button Click ==========
if selected_app == "app1":
    app1()
elif selected_app == "app2":
    app2()
else:
    st.markdown("<h3 style='text-align: center; color: gray;'>Select a service to begin.</h3>", unsafe_allow_html=True)
