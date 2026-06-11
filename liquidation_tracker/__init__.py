"""liquidation-auction-tracker.

A self-contained pipeline that monitors Amazon EU liquidation auctions on
B-Stock, downloads the lot manifests, runs a profitability analysis and alerts
you by WhatsApp and/or email when an auction matches your buying criteria.

This is a standalone showcase project. It uses SQLite for storage and has no
external service dependencies beyond the public B-Stock website, the free
CallMeBot WhatsApp API and (optionally) an SMTP account.
"""

__version__ = "0.1.0"
