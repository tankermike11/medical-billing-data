#!/usr/bin/env python3
"""MASA Billing Data Pilot — ingestion CLI.

Usage:
  python cli.py run a01_icd10cm
  python cli.py run-all a_codes
  python cli.py status
  python cli.py list
"""
import importlib
import sys
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

app = typer.Typer(help="MASA public-data ingestion pilot")
console = Console()

FAMILY_PREFIXES = {
    "a_codes":      ["a01_icd10cm", "a02_icd10pcs", "a03_hcpcs", "a04_cpt_handling",
                     "a05_carc", "a06_rarc", "a07_revenue", "a08_pos",
                     "a09_modifiers", "a10_ms_drg", "a11_ndc"],
    "b_marketplace": ["b01_qhp_landscape", "b02_plan_attributes_puf",
                      "b03_bencs_puf", "b04_sadp_puf", "b05_marketplace_api",
                      "b06_ca_coveredca"],
    "c_ambulance":  ["c01_ambulance_fee_schedule", "c02_physician_fee_schedule",
                     "c04_ma_landscape", "c06_ncci_edits"],
    "e_rules":      ["e01_nsa_rules", "e02_ncd_ambulance", "e03_medicare_appeals", "e04_aca_appeals"],
    "f_sbc":        ["f01_url_crawler", "f02_downloader", "f03_parser"],
}

_SID_TO_FAMILY = {sid: fam for fam, sids in FAMILY_PREFIXES.items() for sid in sids}

INGESTOR_MAP = {
    "a01_icd10cm":          "ingestors.a_codes.a01_icd10cm.run",
    "a02_icd10pcs":         "ingestors.a_codes.a02_icd10pcs.run",
    "a03_hcpcs":            "ingestors.a_codes.a03_hcpcs.run",
    "a04_cpt_handling":     "ingestors.a_codes.a04_cpt_handling.run",
    "a05_carc":             "ingestors.a_codes.a05_carc.run",
    "a06_rarc":             "ingestors.a_codes.a06_rarc.run",
    "a07_revenue":          "ingestors.a_codes.a07_revenue.run",
    "a08_pos":              "ingestors.a_codes.a08_pos.run",
    "a09_modifiers":        "ingestors.a_codes.a09_modifiers.run",
    "a10_ms_drg":           "ingestors.a_codes.a10_ms_drg.run",
    "a11_ndc":              "ingestors.a_codes.a11_ndc.run",
    "b01_qhp_landscape":    "ingestors.b_marketplace.b01_qhp_landscape.run",
    "b02_plan_attributes_puf": "ingestors.b_marketplace.b02_plan_attributes_puf.run",
    "b03_bencs_puf":        "ingestors.b_marketplace.b03_bencs_puf.run",
    "b04_sadp_puf":         "ingestors.b_marketplace.b04_sadp_puf.run",
    "b05_marketplace_api":  "ingestors.b_marketplace.b05_marketplace_api.run",
    "b06_ca_coveredca":     "ingestors.b_marketplace.b06_ca_coveredca.run",
    "c01_ambulance_fee_schedule": "ingestors.c_ambulance.c01_ambulance_fee_schedule.run",
    "c02_physician_fee_schedule": "ingestors.c_ambulance.c02_physician_fee_schedule.run",
    "c04_ma_landscape":           "ingestors.c_ambulance.c04_ma_landscape.run",
    "c06_ncci_edits":             "ingestors.c_ambulance.c06_ncci_edits.run",
    "e01_nsa_rules":              "ingestors.e_rules.e01_nsa_rules.run",
    "e02_ncd_ambulance":          "ingestors.e_rules.e02_ncd_ambulance.run",
    "e03_medicare_appeals":       "ingestors.e_rules.e03_medicare_appeals.run",
    "e04_aca_appeals":            "ingestors.e_rules.e04_aca_appeals.run",
    "f01_url_crawler":      "ingestors.f_sbc.f01_url_crawler.run",
    "f02_downloader":       "ingestors.f_sbc.f02_downloader.run",
    "f03_parser":           "ingestors.f_sbc.f03_parser.run",
}


@app.command()
def run(source_id: str = typer.Argument(..., help="Source ID from source_registry.yaml")):
    """Run a single ingestor by source_id."""
    if source_id not in INGESTOR_MAP:
        console.print(f"[red]Unknown source_id: {source_id}[/red]")
        console.print("Run [bold]python cli.py list[/bold] to see available sources.")
        raise typer.Exit(1)
    mod = importlib.import_module(INGESTOR_MAP[source_id])
    mod.main()


@app.command("run-all")
def run_all(family: str = typer.Argument(..., help="Family name: a_codes, b_marketplace, f_sbc")):
    """Run all ingestors in a family in order."""
    if family not in FAMILY_PREFIXES:
        console.print(f"[red]Unknown family: {family}[/red]. Choose from: {list(FAMILY_PREFIXES)}")
        raise typer.Exit(1)
    for sid in FAMILY_PREFIXES[family]:
        console.rule(f"[bold]{sid}[/bold]")
        try:
            mod = importlib.import_module(INGESTOR_MAP[sid])
            mod.main()
        except Exception as e:
            console.print(f"[red]{sid} failed: {e}[/red]")


@app.command()
def list():
    """List all available ingestor source IDs."""
    t = Table("Family", "Source ID", "Module")
    for sid, mod in INGESTOR_MAP.items():
        family = _SID_TO_FAMILY.get(sid, "unknown")
        t.add_row(family, sid, mod)
    console.print(t)


@app.command()
def status():
    """Show ingestion status from the SQLite database."""
    from ingestors.core.db import get_conn
    conn = get_conn()
    rows = conn.execute(
        "SELECT source_id, status, row_count, retrieved_at, notes FROM dataset_releases ORDER BY id DESC LIMIT 50"
    ).fetchall()
    if not rows:
        console.print("[yellow]No releases recorded yet.[/yellow]")
        return
    t = Table("Source ID", "Status", "Rows", "Retrieved At", "Notes")
    for r in rows:
        color = "green" if r["status"] == "success" else "red"
        t.add_row(r["source_id"], f"[{color}]{r['status']}[/{color}]",
                  str(r["row_count"]), r["retrieved_at"], r["notes"] or "")
    console.print(t)


if __name__ == "__main__":
    app()
