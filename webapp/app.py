# sdi_services.py   ── run with  ➜  streamlit run sdi_services.py
import streamlit as st
import os, io, re, json, base64, tempfile
from datetime import datetime
from zipfile import ZipFile
from pathlib import Path
import pandas as pd
import numpy as np
import cv2                                  # still used by APP 1 crop helper
from pdf2image import convert_from_bytes    # used by APP 1 & APP 3
from PIL import Image
import matplotlib.pyplot as plt
import seaborn as sns
import openai

# ─────────── AUTHENTICATION ───────────
if "authenticated" not in st.session_state:
    st.session_state.authenticated = False

def login_screen():
    st.markdown("<h2 style='text-align:center;'>🔐 SDI SERVICES – Login</h2>", unsafe_allow_html=True)
    with st.form("login_form", clear_on_submit=False):
        pwd = st.text_input("Enter password", type="password")
        submitted = st.form_submit_button("Enter")
        if submitted:
            if pwd == st.secrets["PASSWORD_SDI_ENTER"]:
                st.session_state.authenticated = True
                st.rerun()
            else:
                st.error("❌ Incorrect password")

if not st.session_state.authenticated:
    login_screen()
    st.stop()

# ─────────── GLOBAL CONFIG ───────────
st.set_page_config(page_title="SDI SERVICES", layout="wide", initial_sidebar_state="collapsed")
openai.api_key = st.secrets["OPENAI_API_KEY"]

PROMPT_FILE_APP1 = "webapp/receipt_prompt.txt"
with open(PROMPT_FILE_APP1, "r", encoding="utf-8") as f:
    RECEIPT_PROMPT_APP1 = f.read()

PROMPT_FILE_APP3 = Path(__file__).with_name("promptapp3.txt")
with open(PROMPT_FILE_APP3, "r", encoding="utf-8") as f:
    RECEIPT_PROMPT_APP3 = f.read()

# ─────────── SESSION STATE ───────────
if "active_app" not in st.session_state:
    st.session_state.active_app = None  # None = main menu

# ─────────── COMMON HELPER (JSON extractor) ───────────
def _extract_json_from_response(text_response: str):
    match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text_response, re.DOTALL)
    return match.group(1) if match else text_response.strip()

# ─────────── APP 1 – Receipt Reader & Renamer ───────────
def receipt_reader_app():
    st.title("App 1: Receipt Renamer")
    st.markdown("Upload your receipts (images or PDFs). We’ll extract the data, rename the files, and return a ZIP + Excel summary.")

    def _parse_receipt(image_bytes, prompt):
        b64 = base64.b64encode(image_bytes).decode("utf-8")
        resp = openai.chat.completions.create(
            model="gpt-4.1-mini",
            messages=[
                {"role": "system", "content": prompt},
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "Extract the receipt info as described."},
                        {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}}
                    ]
                }
            ],
            max_tokens=512
        )
        return json.loads(_extract_json_from_response(resp.choices[0].message.content))

    def _rename_file(data, ext):
        safe = lambda s: "".join(c for c in str(s) if c.isalnum() or c in "-_ ")
        date    = data.get("Date", "") or ""
        payee   = safe(data.get("Name", "") or "")
        ref     = safe(data.get("Ref", "")  or "")
        project = safe(data.get("Project", "") or "")
        pm      = data.get("Payment Method", "") or ""
        last4   = (re.search(r"\*+(\d{4})", pm) or re.match(r".*?(\d{4})$", pm) or [None]*2)[1] or "XXXX"
        return f"{date} CC{last4} {payee} Inv {ref} - {project}{ext}"

    with st.form("upload_receipts_form"):
        files_up = st.file_uploader("Choose receipt files", type=["jpg","jpeg","png","webp","pdf"], accept_multiple_files=True)
        submitted = st.form_submit_button("Process Receipts")

    if submitted and files_up:
        with st.spinner("Processing…"):
            rows, zip_buf = [], io.BytesIO()
            with ZipFile(zip_buf, "w") as zipf:
                for f in files_up:
                    ext = os.path.splitext(f.name)[-1].lower()
                    if ext == ".pdf":
                        for img in convert_from_bytes(f.read(), fmt="jpeg"):
                            b = io.BytesIO()
                            img.save(b, format="JPEG"); img_bytes = b.getvalue()
                            data = _parse_receipt(img_bytes, RECEIPT_PROMPT_APP1)
                            if data:
                                rows.append(data)
                                zipf.writestr(_rename_file(data, ".jpg"), img_bytes)
                    else:
                        img_bytes = f.read()
                        data = _parse_receipt(img_bytes, RECEIPT_PROMPT_APP1)
                        if data:
                            rows.append(data)
                            zipf.writestr(_rename_file(data, ext), img_bytes)

                df = pd.DataFrame(rows)
                xlsx = io.BytesIO(); df.to_excel(xlsx, index=False)
                zipf.writestr("Receipts Summary.xlsx", xlsx.getvalue())

            zip_buf.seek(0)

        st.success("Done! Download everything below.")
        st.download_button(
            "Download ZIP",
            zip_buf,
            file_name=f"Receipts_{datetime.now().strftime('%Y%m%d_%H%M%S')}.zip"
        )
    if st.button("⬅️  Main menu"):
        st.session_state.active_app = None
        st.rerun()

# ─────────── APP 2 – P&L by Project Analyzer ───────────
def pnl_summary_app():
    st.title("App 2: P&L by Project [Not Working]")
    st.markdown("Upload one or more **numeric‑formatted** QuickBooks P&L by Customer reports.")

    def _extract_with_month_from_b6(file_obj, file_name):
        df_src = pd.read_excel(file_obj, sheet_name=0, header=None)
        proj = df_src.iloc[4, 1:].fillna("").astype(str).str.strip()
        valid = [i for i, n in enumerate(proj, start=1) if n and "total" not in n.lower() and "(" in n and ")" in n]
        month = str(df_src.iloc[5, 1]).strip() if pd.notna(df_src.iloc[5, 1]) else file_name
        sales_idx = df_src[df_src.iloc[:,0].astype(str).str.strip()=="61100 Contract Sales"].index
        cogs_idx  = df_src[df_src.iloc[:,0].astype(str).str.strip()=="Total Cost of Goods Sold"].index
        if sales_idx.empty or cogs_idx.empty:
            return pd.DataFrame()
        s_row = df_src.iloc[sales_idx[0], valid].fillna(0).astype(float)
        c_row = df_src.iloc[cogs_idx[0],  valid].fillna(0).astype(float)
        sales_df = pd.DataFrame({"Project":[proj[i]+" - Income" for i in valid], month:s_row.values})
        cogs_df  = pd.DataFrame({"Project":[proj[i]+" - Cost"   for i in valid], month:c_row.values})
        return pd.concat([sales_df, cogs_df], axis=0).reset_index(drop=True)

    files_up = st.file_uploader("Upload Excel files", type=["xlsx"], accept_multiple_files=True)

    if files_up:
        df_all = pd.concat([_extract_with_month_from_b6(f, f.name) for f in files_up], axis=0)
        piv = df_all.pivot_table(index="Project", aggfunc='first').fillna(0)
        projects = {i.replace(" - Income","").replace(" - Cost","") for i in piv.index}

        profit = {p: piv.loc.get(p+" - Income", pd.Series(0, index=piv.columns))
                    - piv.loc.get(p+" - Cost",   pd.Series(0, index=piv.columns))
                  for p in projects}
        df_profit = pd.DataFrame.from_dict(profit, orient="index").fillna(0)
        piv.loc["Total"] = piv.sum(); df_profit.loc["Total"] = df_profit.sum()

        st.success("Data processed 🎉")
        st.subheader("📌 Project Summary"); st.dataframe(piv.style.format("${:,.2f}"))
        st.subheader("📌 Profit & Loss");   st.dataframe(df_profit.style.format("${:,.2f}").applymap(
            lambda v: "background-color:#ffe6e6" if v<0 else ""))
        with st.expander("📈  Show project charts"):
            sel = st.selectbox("Choose a project", sorted(projects))
            if st.button("Generate charts"):
                inc = piv.loc.get(sel+" - Income", pd.Series(0, index=piv.columns))
                cost= piv.loc.get(sel+" - Cost",   pd.Series(0, index=piv.columns))
                plot_df = pd.DataFrame({
                    "Month": piv.columns,
                    "Income": inc.values,
                    "Cost": cost.values,
                    "Net": inc.values - cost.values,
                    "Margin %": ((inc-cost)/inc.replace(0,np.nan))*100
                })
                st.line_chart(plot_df.set_index("Month")[["Income","Cost"]])
                st.bar_chart(plot_df.set_index("Month")["Net"])
                st.line_chart(plot_df.set_index("Month")["Margin %"])

        out = io.BytesIO()
        with pd.ExcelWriter(out, engine="openpyxl") as w:
            piv.to_excel(w, sheet_name="Summary")
            df_profit.to_excel(w, sheet_name="Profit")
        st.download_button("📥  Download Excel", out.getvalue(), file_name="project_summary.xlsx")

    if st.button("⬅️  Main menu"):
        st.session_state.active_app = None
        st.rerun()

# ─────────── APP 3 – QuickBooks Expenses Importer ───────────
def expense_importer_app():
    st.title("App 3: Importer")
    st.markdown(
        "Upload receipt images or PDFs. You’ll get a ZIP containing renamed images "
        "and an Excel formatted for QBO import."
    )

    # -------------- helpers --------------
    def _parse_receipt(img_bytes):
        b64 = base64.b64encode(img_bytes).decode("utf-8")
        resp = openai.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": RECEIPT_PROMPT_APP3},
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "Extract the receipt info as described."},
                        {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}}
                    ]
                }
            ],
            max_tokens=512
        )
        return json.loads(_extract_json_from_response(resp.choices[0].message.content))

    def _rename(data, ext):
        clean = lambda s: "".join(c for c in str(s) if c.isalnum() or c in "-_ ")
        date    = data.get("Payment date", "") or "nodate"
        payee   = clean(data.get("Payee", "") or "nopayee")
        ref     = clean(data.get("Reference", "") or "noref")
        project = clean(data.get("Project", "") or "")
        return f"{date} {payee} Inv {ref} – {project}{ext}"

    def _ensure_jpg(img_bytes, orig_ext):
        """Return (jpg_bytes, '.jpg'), converting if needed."""
        if orig_ext in (".jpg", ".jpeg"):
            return img_bytes, ".jpg"
        # convert with Pillow
        from PIL import Image
        img = Image.open(io.BytesIO(img_bytes)).convert("RGB")
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=90)
        return buf.getvalue(), ".jpg"
    # -------------------------------------

    # Always-visible uploader + button (form)
    with st.form("import_form"):
        uploads = st.file_uploader(
            "Upload receipts (images or PDFs)",
            type=None,                   # ← accept anything
            accept_multiple_files=True
        )
        submitted = st.form_submit_button("Process Receipts 🚀")

    if submitted:
        if not uploads:
            st.warning("Please upload at least one file."); return

        with st.spinner("Parsing receipts, please wait…"):
            tmpdir  = tempfile.mkdtemp()
            summary = []

            for f in uploads:
                ext = os.path.splitext(f.name)[-1].lower()

                def _handle(img_b, std_ext):
                    try:
                        data = _parse_receipt(img_b)
                        new_name = _rename(data, std_ext)
                        with open(os.path.join(tmpdir, new_name), "wb") as im:
                            im.write(img_b)
                        summary.append(data)
                    except Exception as e:
                        st.error(f"❌ Error parsing {f.name}: {e}")

                if ext == ".pdf":
                    for pg in convert_from_bytes(f.read(), fmt="jpeg"):
                        buf = io.BytesIO(); pg.save(buf, format="JPEG")
                        _handle(buf.getvalue(), ".jpg")
                else:
                    img_b, std_ext = _ensure_jpg(f.read(), ext)  # convert if needed
                    _handle(img_b, std_ext)

            # Excel summary
            df_sum = pd.DataFrame(summary)
            excel_buf = io.BytesIO(); df_sum.to_excel(excel_buf, index=False)
            with open(os.path.join(tmpdir, "QBO Importer.xlsx"), "wb") as xl:
                xl.write(excel_buf.getvalue())

            # ZIP everything
            zip_buf = io.BytesIO()
            with ZipFile(zip_buf, "w") as z:
                for root, _, files in os.walk(tmpdir):
                    for fn in files:
                        z.write(os.path.join(root, fn), fn)
            zip_buf.seek(0)

        st.success("Done! Download your ZIP below.")
        st.download_button(
            "📦 Download Processed ZIP",
            zip_buf,
            file_name=f"Processed_Receipts_{datetime.now().strftime('%Y%m%d_%H%M%S')}.zip"
        )

    if st.button("⬅️  Main menu"):
        st.session_state.active_app = None
        st.rerun()


# ─────────── MAIN MENU UI ───────────
def main_menu():
    st.markdown(
        """
        <style>
        body { background-color: #000000; color: #ffffff; }
        div.stButton > button {
            background:#ffffff; color:#000; border:none; border-radius:30px; padding:18px 60px;
            font-weight:700; letter-spacing:3px;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )
    st.markdown("<h1 style='text-align:center; letter-spacing:8px;'>SDI SERVICES - BETA VERSION</h1>", unsafe_allow_html=True)
    col1, col2 = st.columns([1,1], gap="large")

    with col1:
        if st.button("App1: Receipt Renamer"):
            st.session_state.active_app = 1; st.rerun()
        if st.button("App2: Importer"):
            st.session_state.active_app = 3; st.rerun()
        if st.button("APP 5"):
            st.info("Coming soon…")

    with col2:
        if st.button("App 2: [Not working]"):
            st.session_state.active_app = 2; st.rerun()
        if st.button("APP 4"):
            st.info("Coming soon…")
        if st.button("APP 6"):
            st.info("Coming soon…")

# ─────────── ROUTER ───────────
if st.session_state.active_app == 1:
    receipt_reader_app()
elif st.session_state.active_app == 2:
    pnl_summary_app()
elif st.session_state.active_app == 3:
    expense_importer_app()
else:
    main_menu()
