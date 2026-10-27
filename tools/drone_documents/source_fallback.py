"""Bounded, explicitly labelled source rendering when generative checks fail."""

import re


def verify_proofs(proofs: list[dict], evidence: list[dict]) -> bool:
    if not proofs:
        return False
    for proof in proofs:
        row = evidence[proof["source_index"] - 1]
        if proof["kind"] == "source_excerpt":
            if row["text"][proof["start"] : proof["end"]] != proof["text"]:
                return False
        elif proof["kind"] == "structured_claim":
            if row.get("evidence_metadata", {}).get("claim") != proof["claim"]:
                return False
            claim = proof["claim"]
            number = str(claim["value"]["number"]).removesuffix(".0")
            if not re.search(r"(?<![\d.])" + re.escape(number) + r"(?![\d.])", row["text"]):
                return False
            speed = claim["conditions"].get("test_speed_kmh")
            if speed is not None and str(speed) not in row["text"]:
                return False
        else:
            return False
    return True


def source_fallback(question: str, evidence: list[dict]) -> dict | None:
    proofs, lines = [], ["自动生成的总结未通过核验，以下直接展示来源记录：", ""]
    if "返航" in question and "障碍" in question:
        for i, row in enumerate(evidence, 1):
            if row["chunk_type"] != "text":
                continue
            text = row["text"]
            start = text.find("返航避障过程")
            if start >= 0:
                end = text.find("返航上升过程中", start + 6)
                end = len(text) if end < 0 else end
                quote = text[start:end]
                if "上升过程中" in quote and "前进过程中" in quote:
                    proofs.append(
                        {
                            "kind": "source_excerpt",
                            "source_index": i,
                            "start": start,
                            "end": end,
                            "text": quote,
                        }
                    )
                    lines += [quote + f"[{i}]", ""]
        if not proofs:
            return None
        lines += ["相关原文限制：", ""]
        seen = set()
        for i, row in enumerate(evidence, 1):
            if row["chunk_type"] != "text":
                continue
            if "返航" not in row.get("section_path", ""):
                continue
            for match in re.finditer(r"[^。]*(?:光照|最大避障速度|限飞高度)[^。]*。", row["text"]):
                if match[0] in seen:
                    continue
                seen.add(match[0])
                proofs.append(
                    {
                        "kind": "source_excerpt",
                        "source_index": i,
                        "start": match.start(),
                        "end": match.end(),
                        "text": match[0],
                    }
                )
                lines += [match[0] + f"[{i}]", ""]
    elif re.search(r"最长飞行|续航|飞行时间|飞多久", question):
        flight_count, weight_done = 0, False
        for i, row in enumerate(evidence, 1):
            claim = row.get("evidence_metadata", {}).get("claim")
            if not claim:
                continue
            if claim.get("comparison_eligible") is False:
                continue
            key = claim["parameter_key"]
            if key == "takeoff_weight" and "重量" in question and not weight_done:
                weight_done = True
                lines += [
                    f"起飞重量：{claim['value']['number']:g} {claim['value']['unit']}[{i}]。",
                    "",
                ]
            elif key == "max_flight_time":
                flight_count += 1
                speed = claim["conditions"].get("test_speed_kmh")
                wind = claim["conditions"].get("wind")
                wind_note = f"；{wind}环境" if wind else ""
                speed_note = f"；原文测试速度 {speed:g} km/h" if speed is not None else ""
                lines += [
                    f"{row['title']}：最长飞行时间 {claim['value']['number']:g} "
                    f"{claim['value']['unit']}{wind_note}{speed_note}[{i}]。",
                    "",
                ]
            else:
                continue
            proofs.append({"kind": "structured_claim", "source_index": i, "claim": claim})
        if flight_count < 2 or ("重量" in question and not weight_done):
            return None
        differences = [
            d
            for row in evidence
            for d in row.get("evidence_metadata", {}).get("source_differences", [])
        ]
        if not differences:
            return None
        lines += [
            "以上来源记载的测试条件不同，差异尚未核实；应保留各自条件，不能合并为统一测试条件。"
        ]
    else:
        return None
    if not verify_proofs(proofs, evidence):
        return None
    return {
        "answer_zh": "\n".join(lines).strip(),
        "proofs": proofs,
        "method": "source_excerpt_or_structured_claim",
        "model_generated": False,
    }
