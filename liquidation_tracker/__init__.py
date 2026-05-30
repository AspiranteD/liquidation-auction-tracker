"""liquidation-auction-tracker.

A self-contained pipeline that monitors Amazon EU liquidation auctions on
B-Stock, downloads the lot manifests, runs a profitability analysis and emails
an alert when an auction matches your buying criteria.

This is a standalone showcase project. It uses SQLite for storage and has no
external service dependencies beyond the public B-Stock website and (optionally)
an SMTP account for email alerts.
"""

__version__ = "0.1.0"
