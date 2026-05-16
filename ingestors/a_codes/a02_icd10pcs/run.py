"""ICD-10-PCS FY2025 ingestor.

Source: CMS annual ZIP — icd10pcs_tables_YYYY.xml
Parses the PCS table XML to enumerate every valid 7-character code.

XML structure:
  pcsTable
    axis pos=1,2,3  (fixed for the whole table — single label each)
    pcsRow          (each row = one valid combination of positions 4-7)
      axis pos=4,5,6,7  (multiple labels each)

Each code is the concatenation of the code attributes from one label
per axis position.  Description is built from the axis label text.

Falls back to icd10pcs_order_*.txt fixed-width format if the ZIP
contains that file instead (e.g. future CMS release format change).
"""
import itertools
import re
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path
import sqlite3

from ingestors.core.pipeline import run_ingestor, now_utc

SOURCE_ID = "a02_icd10pcs"
CODE_TYPE = "ICD10PCS"

_TABLES_RE = re.compile(r"icd10pcs_tables_\d{4}\.xml$", re.IGNORECASE)
_ORDER_RE  = re.compile(r"icd10pcs_order_\d{4}\.txt$",  re.IGNORECASE)


def _find(zf: zipfile.ZipFile, pattern: re.Pattern) -> str | None:
    return next((n for n in zf.namelist() if pattern.search(n)), None)


def _axis_labels(axis_el) -> list[tuple[str, str]]:
    return [(lbl.attrib["code"], lbl.text or "") for lbl in axis_el.findall("label")]


def _parse_xml(xml_bytes: bytes) -> list[tuple]:
    root = ET.fromstring(xml_bytes)
    records = []

    for table in root.findall("pcsTable"):
        fixed: dict[int, tuple[str, str]] = {}
        for ax in table.findall("axis"):
            pos = int(ax.attrib["pos"])
            labels = _axis_labels(ax)
            if labels:
                fixed[pos] = labels[0]

        if len(fixed) < 3:
            continue

        for row in table.findall("pcsRow"):
            row_axes: dict[int, list[tuple[str, str]]] = {}
            for ax in row.findall("axis"):
                pos = int(ax.attrib["pos"])
                row_axes[pos] = _axis_labels(ax)

            if not all(p in row_axes for p in (4, 5, 6, 7)):
                continue

            sec_c, sec_d = fixed[1]
            sys_c, sys_d = fixed[2]
            op_c,  op_d  = fixed[3]

            for (bp_c, bp_d), (ap_c, ap_d), (dv_c, dv_d), (ql_c, ql_d) in itertools.product(
                row_axes[4], row_axes[5], row_axes[6], row_axes[7]
            ):
                code = sec_c + sys_c + op_c + bp_c + ap_c + dv_c + ql_c

                desc_parts = [op_d, bp_d, f"{ap_d} Approach"]
                if dv_d and dv_d != "No Device":
                    desc_parts.insert(2, f"with {dv_d}")
                if ql_d and ql_d != "No Qualifier":
                    desc_parts.append(f"to {ql_d}")
                description = " ".join(desc_parts)
                short_desc = f"{op_d} {bp_d}"

                records.append((code, description, short_desc))

    return records


def _parse_order_txt(lines: list[str]) -> list[tuple]:
    records = []
    for line in lines:
        if len(line) < 16:
            continue
        code = line[6:13].strip()
        if not code:
            continue
        is_header  = line[14].strip() == "0"
        short_desc = line[16:76].strip() if len(line) > 16 else ""
        long_desc  = line[77:].strip()   if len(line) > 77 else ""
        records.append((code, long_desc or short_desc, short_desc, 1 if is_header else 0))
    return records


def ingest(source: dict, local_path: Path, file_hash: str, conn: sqlite3.Connection) -> int:
    retrieved_at = now_utc()
    source_url = source["canonical_url"]

    with zipfile.ZipFile(local_path) as zf:
        order_name  = _find(zf, _ORDER_RE)
        tables_name = _find(zf, _TABLES_RE)

        if order_name:
            with zf.open(order_name) as fh:
                lines = fh.read().decode("latin-1").splitlines()
            parsed = _parse_order_txt(lines)
            db_rows = [
                (CODE_TYPE, code, desc, short, hdr, SOURCE_ID, source_url, retrieved_at)
                for code, desc, short, hdr in parsed
            ]
        elif tables_name:
            with zf.open(tables_name) as fh:
                xml_bytes = fh.read()
            parsed = _parse_xml(xml_bytes)
            db_rows = [
                (CODE_TYPE, code, desc, short, 0, SOURCE_ID, source_url, retrieved_at)
                for code, desc, short in parsed
            ]
        else:
            raise FileNotFoundError(
                f"No icd10pcs_tables_*.xml or icd10pcs_order_*.txt in ZIP. "
                f"Contents: {zf.namelist()[:20]}"
            )

    conn.executemany(
        """INSERT OR REPLACE INTO codes
           (code_type, code, description, short_description, is_header,
            source_id, source_url, retrieved_at)
           VALUES (?,?,?,?,?,?,?,?)""",
        db_rows,
    )
    conn.commit()
    return len(db_rows)


def main():
    run_ingestor(SOURCE_ID, ingest)


if __name__ == "__main__":
    main()
