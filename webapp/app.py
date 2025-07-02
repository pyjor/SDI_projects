# sdi_services.py  ── run with  ➜  streamlit run sdi_services.py
import streamlit as st
import os, io, re, json, base64, tempfile
from datetime import datetime
from zipfile import ZipFile
import pandas as pd
import numpy as np
import cv2
from pdf2image import convert_from_bytes
from PIL import Image
import matplotlib.pyplot as plt
import seaborn as sns
import openai

# ─────────── GLOBAL CONFIG ───────────
st.set_page_config(page_title="SDI SERVICES", layout="wide", initial_sidebar_state="collapsed")
openai.api_key = st.secrets["OPENAI_API_KEY"]
PROMPT_FILE = "webapp/receipt_prompt.txt"
with open(PROMPT_FILE, "r", encoding="utf-8") as f:
    RECEIPT_PROMPT = f.read()

# ─────────── SESSION STATE ───────────
if "active_app" not in st.session_state:
    st.session_state.active_app = None  # None = main menu


# ─────────── HELPER FUNCTIONS FOR APP 1 (Receipts) ───────────
def _extract_json_from_response(text_response: str):
    match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text_response, re.DOTALL)
    return match.group(1) if match else text_response.strip()

def _parse_receipt_with_openai(image_bytes: bytes, prompt: str):
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
    raw_text = response.choices[0].message.content
    try:
        json_str = _extract_json_from_response(raw_text)
        return json.loads(json_str)
    except Exception as e:
        st.warning(f"Error parsing JSON: {e}\nRaw output: {raw_text}")
        return None

def _rename_file(data: dict, ext: str):
    safe = lambda s: "".join(c for c in str(s) if c.isalnum() or c in "-_ ")
    date        = data.get("Date", "") or " "
    payee       = data.get("Name", "") or " "
    ref         = data.get("Ref", "")  or " "
    project     = data.get("Project", "") or " "
    payment_mtd = data.get("Payment Method", "") or ""
    last4 = (re.search(r"\*+(\d{4})", payment_mtd) or re.match(r".*?(\d{4})$", payment_mtd) or [None]*2)[1] or "XXXX"
    return f"{date} CC{last4} {safe(payee)} Inv {safe(ref)} - {safe(project)}{ext}"

# ─────────── MINI-APP 1 ───────────
def receipt_reader_app():
    st.title("Receipt Reader")
    st.markdown("Upload your receipts (images or PDFs). We’ll extract the data, rename the files, and return a ZIP + Excel summary.")

    with st.form("upload_receipts_form"):
        receipt_files = st.file_uploader("Choose receipt files", type=["jpg","jpeg","png","webp","pdf"], accept_multiple_files=True)
        submitted = st.form_submit_button("Process Receipts")

    if submitted and receipt_files:
        with st.spinner("Processing…"):
            rows, zip_buf = [], io.BytesIO()
            with ZipFile(zip_buf, "w") as zipf:
                for f in receipt_files:
                    ext = os.path.splitext(f.name)[-1].lower()
                    if ext == ".pdf":
                        pdf_bytes = f.read()
                        for img in convert_from_bytes(pdf_bytes, fmt="jpeg"):
                            with tempfile.NamedTemporaryFile(suffix=".jpg") as tmp:
                                img.save(tmp.name, format="JPEG")
                                img_bytes = open(tmp.name, "rb").read()
                            data = _parse_receipt_with_openai(img_bytes, RECEIPT_PROMPT)
                            if data:
                                rows.append(data)
                                zipf.writestr(_rename_file(data, ".jpg"), img_bytes)
                    else:
                        img_bytes = f.read()
                        data = _parse_receipt_with_openai(img_bytes, RECEIPT_PROMPT)
                        if data:
                            rows.append(data)
                            zipf.writestr(_rename_file(data, ext), img_bytes)

                df_receipts = pd.DataFrame(rows)
                xlsx_buf = io.BytesIO()
                df_receipts.to_excel(xlsx_buf, index=False)
                zipf.writestr("Receipts Summary.xlsx", xlsx_buf.getvalue())

            zip_buf.seek(0)

        st.success("Done! Download everything below.")
        st.download_button(
            "Download ZIP",
            zip_buf,
            file_name=f"Receipts_{datetime.now().strftime('%Y%m%d_%H%M%S')}.zip"
        )
    st.markdown("— [Back to menu](#)", unsafe_allow_html=True)
    if st.button("⬅️  Main menu"):
        st.session_state.active_app = None
        st.rerun()


# ─────────── MINI-APP 2 ───────────
def pnl_summary_app():
    st.title("QuickBooks P&L Project Summary")
    st.markdown("Upload one or more **numeric-formatted** QuickBooks P&L by Customer reports. You’ll get a summary + P&L tables and charts.")

    def _extract_with_month_from_b6(file_obj, file_name):
        df_source = pd.read_excel(file_obj, sheet_name=0, header=None)
        project_names = df_source.iloc[4, 1:].fillna("").astype(str).str.strip()
        valid_cols = [i for i, n in enumerate(project_names, start=1)
                      if n and "total" not in n.lower() and "(" in n and ")" in n]
        month_cell = df_source.iloc[5, 1]
        month_lbl = str(month_cell).strip() if pd.notna(month_cell) else file_name

        sales_idx = df_source[df_source.iloc[:,0].astype(str).str.strip()=="61100 Contract Sales"].index
        cogs_idx  = df_source[df_source.iloc[:,0].astype(str).str.strip()=="Total Cost of Goods Sold"].index
        if sales_idx.empty or cogs_idx.empty:
            return pd.DataFrame()

        sales_row = df_source.iloc[sales_idx[0], valid_cols].fillna(0).astype(float)
        cogs_row  = df_source.iloc[cogs_idx[0],  valid_cols].fillna(0).astype(float)

        sales_df = pd.DataFrame({"Project":[project_names[i]+" - Income" for i in valid_cols], month_lbl:sales_row.values})
        cogs_df  = pd.DataFrame({"Project":[project_names[i]+" - Cost"   for i in valid_cols], month_lbl:cogs_row.values})
        return pd.concat([sales_df, cogs_df], axis=0).reset_index(drop=True)

    pnl_files = st.file_uploader("Upload Excel files", type=["xlsx"], accept_multiple_files=True)

    if pnl_files:
        all_parts = [ _extract_with_month_from_b6(f, f.name) for f in pnl_files ]
        df_pnl = pd.concat(all_parts, axis=0)
        pivot_df = df_pnl.pivot_table(index="Project", aggfunc='first').fillna(0)

        # Build profit table
        profit_dict = {}
        projects = {idx.replace(" - Income","").replace(" - Cost","") for idx in pivot_df.index}
        for proj in projects:
            inc = pivot_df.loc.get(f"{proj} - Income", pd.Series(0, index=pivot_df.columns))
            cost= pivot_df.loc.get(f"{proj} - Cost",   pd.Series(0, index=pivot_df.columns))
            profit_dict[proj] = inc - cost
        df_profit = pd.DataFrame.from_dict(profit_dict, orient="index").fillna(0)
        pivot_df.loc["Total"]  = pivot_df.sum()
        df_profit.loc["Total"] = df_profit.sum()

        st.success("Data processed 🎉")
        st.subheader("📌 Project Summary")
        st.dataframe(pivot_df.style.format("${:,.2f}"))

        st.subheader("📌 Profit & Loss")
        st.dataframe(
            df_profit.style.format("${:,.2f}")
                     .applymap(lambda v: "background-color:#ffe6e6" if v<0 else "")
        )

        with st.expander("📈  Show project charts"):
            chosen = st.selectbox("Choose a project", sorted(projects))
            if st.button("Generate charts"):
                inc = pivot_df.loc.get(f"{chosen} - Income", pd.Series(0, index=pivot_df.columns))
                cost= pivot_df.loc.get(f"{chosen} - Cost",   pd.Series(0, index=pivot_df.columns))
                df_chart = pd.DataFrame({
                    "Month": pivot_df.columns,
                    "Income": inc.values,
                    "Cost": cost.values,
                    "Net": inc.values - cost.values,
                    "Margin %": ( (inc-cost) / inc.replace(0,np.nan) ) * 100
                })
                st.line_chart(df_chart.set_index("Month")[["Income","Cost"]])
                st.bar_chart(df_chart.set_index("Month")["Net"])
                st.line_chart(df_chart.set_index("Month")["Margin %"])

        # download
        buf = io.BytesIO()
        with pd.ExcelWriter(buf, engine="openpyxl") as w:
            pivot_df.to_excel(w, sheet_name="Summary")
            df_profit.to_excel(w, sheet_name="Profit")
        st.download_button(
            "📥  Download Excel",
            buf.getvalue(),
            file_name="project_summary.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )
    st.markdown("— [Back to menu](#)", unsafe_allow_html=True)
    if st.button("⬅️  Main menu"):
        st.session_state.active_app = None
        st.rerun()


# ─────────── MAIN MENU UI ───────────
def main_menu():
    st.markdown(
        """
        <style>
        body { background-color: #000000; color: #ffffff; }
        .button-grid  { display: flex; flex-wrap: wrap; justify-content: center; gap: 40px; margin-top:40px;}
        .button-col   { display: flex; flex-direction: column; gap: 30px;}
        div.stButton > button {
            background: #ffffff; color:#000; border:none; border-radius:30px; padding:18px 60px;
            font-weight:700; letter-spacing:3px;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )
    st.markdown("<h1 style='text-align:center; letter-spacing:8px;'>SDI SERVICES</h1>", unsafe_allow_html=True)
    col1, col2 = st.columns([1,1], gap="large")

    with col1:
        if st.button("Receipt Reader and Renamer"):
            st.session_state.active_app = 1
            st.rerun()
        if st.button("APP 3"):
            st.info("Coming soon…")
        if st.button("APP 5"):
            st.info("Coming soon…")

    with col2:
        if st.button("PnL By Project Analyzer"):
            st.session_state.active_app = 2
            st.rerun()
        if st.button("APP 4"):
            st.info("Coming soon…")
        if st.button("APP 6"):
            st.info("Coming soon…")


# ─────────── ROUTER ───────────
if st.session_state.active_app == 1:
    receipt_reader_app()
elif st.session_state.active_app == 2:
    pnl_summary_app()
else:
    main_menu()
