import csv
import json
import re
import sys
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from functools import cache
from html import unescape
from math import isclose
from pathlib import Path
from statistics import fmean, pstdev
from urllib.parse import quote, urlencode, urljoin
from urllib.request import Request, urlopen

startYear = 2020
minRatings = 0
minStudents = 0
outputPath = Path("tamu_teachers.csv")

anexUrl = "https://anex.us/grades/getData/"
catalogUrl = "https://catalog.tamu.edu"
courseSearchUrl = f"{catalogUrl}/course-search/"
rmpUrl = "https://www.ratemyprofessors.com/graphql"
tamuSchoolId = "U2Nob29sLTEwMDM="  # Rate My Professors school 1003.
gradeCols = ("A", "B", "C", "D", "F", "Q")
semesterOrder = {"SPRING": 0, "SUMMER": 1, "FALL": 2}
scorePriorCount = 100
gpaWeight = 0.6
rmpWeight = 0.4
outputFields = (
    "course", "instructor", "gpa", "w_rate", "sections", "total_graded",
    *gradeCols, "terms_taught", "rmp_rating", "rmp_difficulty",
    "rmp_num_ratings", "rmp_would_again", "rmp_url", "kenny_score",
)
rmpQuery = """
query TeacherSearchQuery($text: String!, $schoolID: ID!) {
  newSearch {
    teachers(query: {text: $text, schoolID: $schoolID}, first: 5) {
      edges {
        node {
          firstName lastName avgRating avgDifficulty numRatings
          wouldTakeAgainPercent legacyId
        }
      }
    }
  }
}
"""


def fetchJson(url: str, body: bytes, headers: dict):
    request = Request(url, data=body, headers={"User-Agent": "Mozilla/5.0", **headers})
    with urlopen(request, timeout=15) as response:
        payload = response.read()
    if url == anexUrl and payload.strip() == b'{"classes":]}':
        return {"classes": []}
    return json.loads(payload)


def fetchText(url: str) -> str:
    request = Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urlopen(request, timeout=30) as response:
        return response.read().decode("utf-8")


def courseCodes(text: str) -> set[tuple[str, str]]:
    return set(re.findall(r"\b([A-Z]{2,6})\s+(\d{3,4}[A-Z]?)\b", unescape(text)))


def catalogCourses(html: str) -> set[tuple[str, str]]:
    titles = re.findall(
        r'<(?:h\d|p)\b[^>]*class="courseblocktitle[^"]*"[^>]*>(.*?)</(?:h\d|p)>', html, re.S
    )
    if not titles and 'id="sc_sccoursedescs"' not in html:
        raise ValueError("Unrecognized catalog subject page.")
    courses = set()
    for title in titles:
        courses.update(courseCodes(re.sub(r"<[^>]+>", " ", title)))
    return courses


def discoverCourses(startYear: int) -> list[tuple[str, str]]:
    page = fetchText(courseSearchUrl)
    config = re.search(r"srcDBs:\s*(\[.*?\])", page, re.S)
    if not config:
        raise ValueError("Could not find TAMU's course-search catalog list.")
    courses, coveredYears = set(), set()
    for database in json.loads(config.group(1)):
        year = int(database["code"])
        # The previous edition covers spring/summer of the start year.
        if not startYear - 1 <= year <= date.today().year:
            continue
        data = fetchJson(
            f"{courseSearchUrl}api/?page=fose&route=search",
            quote(json.dumps({"other": {"srcdb": str(year)}, "criteria": []}), safe="").encode(),
            {"Content-Type": "application/json", "Referer": courseSearchUrl},
        )
        if data.get("fatal"):
            raise ValueError(data["fatal"])
        if len(data["results"]) != int(data["count"]):
            raise ValueError(f"Incomplete course list for catalog {year}.")
        for result in data["results"]:
            courses.update(courseCodes(result["code"]))
        coveredYears.add(year)

    archives = fetchText(f"{catalogUrl}/archives/")
    roots = {
        f"/archives/{first}-{last}/"
        for first, last in re.findall(r'href="/archives/(\d{4})-(\d{4})/?"', archives)
        if startYear - 1 <= int(first) <= date.today().year and int(first) not in coveredYears
    }
    subjectPages = set()
    for root in sorted(roots):
        for level in ("undergraduate", "graduate"):
            path = f"{root}{level}/course-descriptions/"
            links = re.findall(r'href="(' + re.escape(path) + r'[a-z0-9-]+/)"', fetchText(urljoin(catalogUrl, path)))
            if not links:
                raise ValueError(f"No subject pages found in {path}.")
            subjectPages.update(urljoin(catalogUrl, link) for link in links)

    print(f"Reading {len(subjectPages)} historical subject pages...", flush=True)
    with ThreadPoolExecutor(max_workers=4) as pool:
        for index, html in enumerate(pool.map(fetchText, sorted(subjectPages)), 1):
            courses.update(catalogCourses(html))
            if index % 100 == 0:
                print(f"  Read {index}/{len(subjectPages)} pages.", flush=True)
    if not courses:
        raise ValueError("No courses.")
    return sorted(courses)


def fetchAnex(dept: str, number: str) -> list[dict]:
    data = fetchJson(
        anexUrl,
        urlencode({"dept": dept.upper(), "number": number}).encode(),
        {"Referer": "https://anex.us/grades/"},
    )
    rows = data["classes"]
    if not isinstance(rows, list):
        raise ValueError("Unexpected grade response.")
    return rows


def instructorName(row: dict) -> str:
    return (row.get("instructor") or row.get("prof") or "").strip()


def filterYears(rows: list[dict], startYear: int = 2020) -> list[dict]:
    """Exclude grades before the start year and future or invalid years."""
    return [
        row
        for row in rows
        if str(row.get("year", "")).isdigit()
        and startYear <= int(row["year"]) <= date.today().year
    ]


def aggregateByInstructor(rows: list[dict]) -> dict[str, dict]:
    totals = defaultdict(lambda: dict.fromkeys(gradeCols, 0) | {"sections": 0})
    terms = defaultdict(set)

    for row in rows:
        name = instructorName(row)
        if not name:
            continue
        totals[name]["sections"] += 1
        for grade in gradeCols:
            totals[name][grade] += int(row.get(grade) or 0)
        year = str(row.get("year") or "").strip()
        semester = str(row.get("semester") or "").strip()
        if year:
            terms[name].add((year, semester))

    results = {}
    for name, counts in totals.items():
        graded = sum(counts[grade] for grade in gradeCols if grade != "Q")
        points = sum(counts[grade] * weight for grade, weight in zip("ABCD", (4, 3, 2, 1)))
        enrolled = graded + counts["Q"]
        sortedTerms = sorted(
            terms[name], key=lambda term: (term[0], semesterOrder.get(term[1].upper(), 9))
        )
        results[name] = {
            **counts,
            "total_graded": graded,
            "gpa": round(points / graded, 2) if graded else None,
            "w_rate": round(counts["Q"] / enrolled * 100, 1) if enrolled else 0.0,
            "terms_taught": "; ".join(f"{semester} {year}".strip() for year, semester in sortedTerms),
        }
    return results


def extractLast(name: str) -> str:
    if "," in name:
        return name.split(",", 1)[0].strip()
    parts = name.split()
    return parts[0] if len(parts[-1]) <= 2 else parts[-1]


def searchRmp(query: str) -> list[dict]:
    try:
        response = fetchJson(
            rmpUrl,
            json.dumps({
                "query": rmpQuery,
                "variables": {"text": query, "schoolID": tamuSchoolId},
            }).encode(),
            {
                "Content-Type": "application/json",
                "Referer": "https://www.ratemyprofessors.com/",
                "Authorization": "Basic dGVzdDp0ZXN0",
            },
        )
        if response.get("errors"):
            raise ValueError(response["errors"])
        edges = response["data"]["newSearch"]["teachers"]["edges"]
        return [edge["node"] for edge in edges]
    except (OSError, ValueError, KeyError, TypeError) as error:
        print(f"RMP search failed for {query}: {error}", file=sys.stderr)
        return []


def nameTokens(name: str) -> set[str]:
    return set(re.findall(r"[a-z]+", name.lower()))


@cache
def lookupRmp(name: str) -> dict:
    last = extractLast(name)
    queries = list(dict.fromkeys([last, name.strip(), *last.split("-")]))
    lastParts = {re.sub(r"[^a-z]", "", part.lower()) for part in (last, *last.split("-"))}
    tokens = nameTokens(name)
    givenNames = tokens - nameTokens(last)

    for index, query in enumerate(queries):
        if index:
            time.sleep(0.4)
        candidates = []
        for node in searchRmp(query):
            rmpLast = re.sub(r"[^a-z]", "", node["lastName"].lower())
            givenMatches = not givenNames or any(
                first.startswith(given)
                for given in givenNames for first in nameTokens(node["firstName"])
            )
            if givenMatches and rmpLast and any(
                part and (part in rmpLast or rmpLast in part) for part in lastParts
            ):
                candidates.append(node)
        if not candidates:
            continue

        node = max(
            candidates,
            key=lambda candidate: len(
                tokens & nameTokens(f"{candidate['firstName']} {candidate['lastName']}")
            ),
        )
        again = node.get("wouldTakeAgainPercent")
        return {
            "rmp_rating": node["avgRating"],
            "rmp_difficulty": node["avgDifficulty"],
            "rmp_num_ratings": node["numRatings"],
            "rmp_would_again": round(again, 1) if again is not None else None,
            "rmp_url": f"https://www.ratemyprofessors.com/professor/{node['legacyId']}",
        }
    return {}


def calculateScores(records: list[dict]) -> None:
    components = []
    for valueKey, countKey in (("gpa", "total_graded"), ("rmp_rating", "rmp_num_ratings")):
        observations = {
            index: (row[valueKey], row[countKey])
            for index, row in enumerate(records)
            if row.get(valueKey) is not None and (row.get(countKey) or 0) > 0
        }
        if not observations:
            components.append({})
            continue
        prior = sum(value * count for value, count in observations.values()) / sum(
            count for _, count in observations.values()
        )
        adjusted = {
            index: (count * value + scorePriorCount * prior) / (count + scorePriorCount)
            for index, (value, count) in observations.items()
        }
        mean, deviation = fmean(adjusted.values()), pstdev(adjusted.values())
        constant = isclose(deviation, 0.0, abs_tol=1e-12)
        components.append({
            index: 0.0 if constant else (value - mean) / deviation
            for index, value in adjusted.items()
        })

    gpaZ, rmpZ = components
    combined = {
        index: gpaWeight * gpaZ[index] + rmpWeight * rmpZ[index]
        for index in range(len(records))
        if index in gpaZ and index in rmpZ
    }
    for row in records:
        row["kenny_score"] = None
    if not combined:
        return
    lowest, highest = min(combined.values()), max(combined.values())
    span = highest - lowest
    tied = isclose(span, 0.0, abs_tol=1e-12)
    for index, score in combined.items():
        records[index]["kenny_score"] = 5.0 if tied else round(10 * (score - lowest) / span, 6)


def main() -> int:

    try:
        courses = sorted(discoverCourses(startYear))
        print(f"Found {len(courses)} courses,")
        saved, failed, teachers = 0, 0, set()
        with outputPath.open("w", newline="", encoding="utf-8") as output:
            writer = csv.DictWriter(output, fieldnames=outputFields)
            writer.writeheader()
            for index, (dept, number) in enumerate(courses, 1):
                course = f"{dept} {number}"
                print(f"[{index}/{len(courses)}] {course}")
                try:
                    rows = filterYears(fetchAnex(dept, number), startYear)
                    instructors = aggregateByInstructor(rows)
                except (OSError, ValueError, KeyError, TypeError) as error:
                    failed += 1
                    print(f"  Could not fetch grades: {error}")
                    continue
                records = []
                for name, stats in sorted(instructors.items()):
                    if stats["total_graded"] < minStudents:
                        continue
                    rmp = lookupRmp(name)
                    if (rmp.get("rmp_num_ratings") or 0) < minRatings:
                        continue
                    records.append({"course": course, "instructor": name, **stats, **rmp})
                    teachers.add(name)
                calculateScores(records)
                writer.writerows(records)
                output.flush()
                saved += len(records)
        return 1 if failed or not saved else 0
    except (OSError, ValueError, KeyError, TypeError) as error:
        print(f"Fetch failed: {error}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
