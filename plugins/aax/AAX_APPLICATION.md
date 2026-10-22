# AAX application

> **The canonical copy of this document is `docs/AAX_APPLICATION.md`.**
> This file is a pointer. It exists so that the application (the request for
> the AAX SDK) is found from inside the adapter directory. If the two ever
> disagree, `docs/AAX_APPLICATION.md` is the one that is right — change it
> there, and keep this file to a pointer.

## What the document contains

`docs/AAX_APPLICATION.md` is the application package for the AAX SDK: the
request that goes to the vendor, and the engineering plan that goes with it.

| Section | What is in it |
|---|---|
| 0 | the ask in one screen, and the text of the request |
| 1 | what is being asked for (the SDK, the validation, the licence terms, the access the adapter needs) and why |
| 2 | the AAX wrapper plan: the manufacturer id, the product id scheme, the plug id scheme, the alphabet and length rules, and the generated table of all 47 modules |
| 3 | the page-table contract: our XML schema, the layout rule, and a worked example |
| 4 | the label rules: the discrete-control classification and the truncation ladder |
| 5 | what the adapter implements: the interfaces, the state chunk, the editor, the parameter proxy |
| 6 | the build and validation plan, including how the adapter is verified **without** the SDK |
| 7 | the timeline: what can be done before the SDK arrives, what is blocked, and the ordered steps once access is granted |
| 8 | the fallback plan: the plug-in matrix, the effort, and the user-facing message |

## The two things to know before reading it

1. **The SDK is a hard external dependency with a long lead time.** Nothing
   else in the plug-in layer needs it; the request is the only item on the
   critical path that is not ours to execute. That is why the document is
   written to be sent as it stands.

2. **The identifiers, the parameter ids, the page layout and the label rules
   are already generated and checked in this repository.** They do not depend
   on the SDK, which is why they are generated rather than written down.

## The tool that backs the document

```sh
python tools/aax_application_checklist.py            # the identifier table, on stdout
python tools/aax_application_checklist.py --format report   # table + summary
python tools/aax_application_checklist.py --format json     # machine readable
```

It reads the canonical parameter table (`aurcore/schema/params.tsv`), derives
the AAX identifier triple of every module, validates every identifier
against the AAX alphabet and length rules, and exits non-zero when one of
them is broken. The table in section 2.5 of the document is that command's
output — regenerate it rather than editing it.

The design of the AAX adapter itself — identifiers, parameter ids, pages and
labels — is in `AAX_ADAPTER_NOTES.md` next to this file.
