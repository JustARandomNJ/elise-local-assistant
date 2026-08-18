from __future__ import annotations
import argparse, json
from pathlib import Path
from knowledge.store import KnowledgeStore

def evaluate(store: KnowledgeStore, cases: list[dict]) -> dict[str,float]:
    top1=top3=expected=negatives=correct_empty=lookups=irrelevant=0
    for case in cases:
        hits=store.search(case["query"],3); wanted=case.get("expected_title")
        if wanted is None: negatives+=1; correct_empty+=not bool(hits); continue
        expected+=1; titles=[h.title.casefold() for h in hits]; target=wanted.casefold()
        top1+=bool(titles and titles[0]==target); top3+=target in titles; lookups+=1; irrelevant+=not bool(hits)
    return {"top_1_accuracy":top1/expected if expected else 0,"top_3_accuracy":top3/expected if expected else 0,"no_result_precision":correct_empty/negatives if negatives else 0,"irrelevant_lookup_rate":irrelevant/lookups if lookups else 0}

if __name__=="__main__":
    parser=argparse.ArgumentParser(); parser.add_argument("--database",type=Path,default=Path("data/knowledge.db")); parser.add_argument("--cases",type=Path,default=Path("knowledge_evaluation.json")); args=parser.parse_args()
    print(json.dumps(evaluate(KnowledgeStore(args.database),json.loads(args.cases.read_text(encoding="utf-8"))),indent=2))
