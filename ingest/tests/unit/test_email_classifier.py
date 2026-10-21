"""Financial sender classification — iter-18: *.bank.in and bank subdomains (audit A3 13.14, R.email-bankin)."""
import pytest

from modules.email.classifier import classify_email_financial, get_sender_label


@pytest.mark.parametrize("sender,subject,snippet,label", [
    # the audit's failing case: .bank.in domain + a subject with no financial keyword
    ("alerts@axis.bank.in", "Important information", "Spent INR 979 using Axis Bank Credit Car", "Axis Bank"),
    ("alerts@hdfcbank.bank.in", "Missed Call from your HDFC Bank Relationship Manager", "", "HDFC Bank"),
    ("credit_cards@icici.bank.in", "Your ICICI Bank Credit Card XX0026", "", "ICICI Bank"),
    ("cbsalerts.sbi@alerts.sbi.bank.in", "CBSSBI ALERT", "Greetings from SBI ! Dear customer, EMI due", "SBI"),
    # bank subdomains of an already-listed domain used to miss on exact matching
    ("cbsalerts.sbi@alerts.sbi.co.in", "CBSSBI ALERT", "Greetings from SBI !", "SBI"),
    ("services@custcomm.icicibank.com", "One number and one e-mail ID to contact ICICI Bank", "", "ICICI Bank"),
    ("info@digital.axisbankmail.bank.in", "Celebrate with offers of the week", "", "Axis Bank"),
    # any bank on the restricted bank.in zone
    ("customer-support@equitas.bank.in", "Welcome", "", "Equitas Bank"),
])
def test_bank_senders_are_financial(sender, subject, snippet, label):
    assert classify_email_financial(sender, subject, snippet) is True
    assert get_sender_label(sender) == label


@pytest.mark.parametrize("sender", [
    "newsletter@fakebank.in",            # not under bank.in
    "promo@bank.in.example.com",         # bank.in as a subdomain of something else
    "hello@notion.so",
])
def test_lookalike_senders_are_not(sender):
    assert classify_email_financial(sender, "Weekly digest", "Here is what happened") is False
    assert get_sender_label(sender) is None
