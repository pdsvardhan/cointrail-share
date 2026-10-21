from modules.pdf.parsers.base import Transaction, StatementParser
from modules.pdf.parsers.hdfc import HDFCParser
from modules.pdf.parsers.icici import ICICIParser
from modules.pdf.parsers.sbi import SBIParser
from modules.pdf.parsers.axis import AxisParser
from modules.pdf.parsers.hsbc import HSBCParser

PARSERS = {
    "hdfc": HDFCParser(),
    "icici": ICICIParser(),
    "sbi": SBIParser(),
    "axis": AxisParser(),
    "hsbc": HSBCParser(),
}

def get_parser(bank):
    parser = PARSERS.get(bank.lower())
    if not parser:
        raise ValueError(f"No parser for bank: {bank}")
    return parser
