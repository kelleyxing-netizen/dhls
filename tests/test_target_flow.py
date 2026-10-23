"""Check that the proposed diagram cannot bypass its review gates."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from dhls.core.target_flow import NODES, EDGES


def reachable(blocked=()):
    seen=set()
    queue=["A01"]
    while queue:
        node=queue.pop()
        if node in seen or node in blocked:
            continue
        seen.add(node)
        queue.extend(t for s,t,_,_ in EDGES if s==node)
    return seen


class TargetFlowTests(unittest.TestCase):
    def test_nodes_and_yes_no_branches_are_complete(self):
        ids={node.id for node in NODES}
        self.assertEqual(len(ids),len(NODES))
        self.assertEqual(ids,reachable())
        self.assertTrue(all(s in ids and t in ids for s,t,_,_ in EDGES))
        for node in NODES:
            if node.kind=="decision":
                branches=[label.split()[0] for s,_,label,_ in EDGES if s==node.id]
                self.assertCountEqual(["是","否"],branches)

    def test_publication_and_business_execution_require_human_review(self):
        without_review=reachable(blocked={"A12"})
        self.assertNotIn("A15",without_review)
        self.assertNotIn("A16",without_review)
        incoming=[(s,label) for s,t,label,_ in EDGES if t=="A15"]
        self.assertEqual([("A13","是")],incoming)
        self.assertEqual("human",next(n.kind for n in NODES if n.id=="A16"))

    def test_confirmed_adjustments_return_to_version_and_rule_checks(self):
        yes_adjustments=[t for s,t,label,_ in EDGES if s=="A10" and label.startswith("是")]
        self.assertEqual(["A03"],yes_adjustments)
        self.assertNotIn("A15",reachable(blocked={"A03"}))
        self.assertNotIn("A15",reachable(blocked={"A06"}))


if __name__=="__main__":
    unittest.main()
