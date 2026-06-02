OCR replica editing
=====================

The proofing page editor supports two view modes:

Replica (default)
  Image with OCR box overlay on the left; bbox-scaled page replica on the right.
  Click blocks to edit text in place.

Flow
  Classic TipTap rich-text editor with image pane (legacy workflow).
  OCR run in Replica mode syncs text here automatically.

Workflow
--------

1. Open a proofing page.
2. Choose **Tools → OCR** and run OCR on the current page.
3. Review and edit in Replica mode (or switch to Flow for continuous text).
4. **Publish changes** to save a revision with structured ``document`` JSON.

Exports
-------

From the project **Download** page:

- Plain text and TEI XML (uses structured blocks when available)
- PageDocument JSON bundle
- Layout HTML replica export

See :doc:`ocr-api` for the OCR service v2 contract.
