# ScrollWright Dashboard

[[ScrollWright/00 ScrollWright Dashboard|Back to dashboard]]

## Instructions to self
- New reels land here with `status:: inbox` — skim, add thoughts, then flip to
  `status:: done` (or delete the note).
- Same reel saved multiple times collapses into one note automatically.

## Inbox (status = inbox)

```dataview
TABLE WITHOUT ID
  file.link AS "Reel",
  author AS "By",
  type AS "Type",
  tags AS "Tags"
FROM "ScrollWright"
WHERE status = "inbox"
SORT file.ctime DESC
```

## Processed (status = done)

```dataview
TABLE WITHOUT ID
  file.link AS "Reel",
  author AS "By",
  type AS "Type",
  tags AS "Tags"
FROM "ScrollWright"
WHERE status = "done"
SORT file.ctime DESC
```

## By type

```dataview
TABLE WITHOUT ID
  file.link AS "Reel",
  author AS "By",
  tags AS "Tags"
FROM "ScrollWright"
WHERE status != "inbox"
GROUP BY type
```
