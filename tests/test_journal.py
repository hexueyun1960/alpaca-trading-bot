import json
import unittest
from pathlib import Path

from src.journal import TradeJournal
from src.risk import RiskDecision


class TradeJournalTests(unittest.TestCase):
    def test_records_jsonl_event(self):
        path = Path("logs/test_journal.jsonl")
        path.unlink(missing_ok=True)
        self.addCleanup(lambda: path.unlink(missing_ok=True))
        journal = TradeJournal(str(path))

        journal.record("decision", {"risk": RiskDecision(True, "approved")})

        lines = path.read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(lines), 1)

        event = json.loads(lines[0])
        self.assertEqual(event["event_type"], "decision")
        self.assertEqual(event["payload"]["risk"]["approved"], True)


if __name__ == "__main__":
    unittest.main()
