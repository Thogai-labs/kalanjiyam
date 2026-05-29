OCR replica editing
=====================

The proofing page editor supports three view modes:

Split (default)
  Image with OCR box overlay on the left; block list editor on the right.

Replica
  Image with overlay on the left; bbox-scaled page replica on the right.
  Click blocks to edit text in place.

Flow
  Classic TipTap rich-text editor with image pane (legacy workflow).

Workflow
--------

1. Open a proofing page.
2. Choose **Tools → OCR** and run OCR on the current page.
3. Review blocks in Split or Replica mode; edit text and block types.
4. **Publish changes** to save a revision with structured ``document`` JSON.

Exports
-------

From the project **Download** page:

- Plain text and TEI XML (uses structured blocks when available)
- PageDocument JSON bundle
- Layout HTML replica export

See :doc:`ocr-api` for the OCR service v2 contract.
