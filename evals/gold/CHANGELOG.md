# Gold changelog

Gold labels change only with an entry here that gives the reason. The manifest test fails on
any change to a gold file that the manifest does not record.

## 2026-11-11
- Seeds 7, 11 and 13 generated, before any extraction code existed.

## 2026-11-11, second entry
- A household member's call carried no email, but its label listed one. Calls now never get
  an email in the plan, so the label matches the message. Repeats of those leads are planned
  from the contacts the lead really has, which changes the random draws after them, so all
  three seeds were regenerated. A new test checks that every gold phone, email, name and item
  appears in its message. No rules code had run on seeds 11 or 13.
