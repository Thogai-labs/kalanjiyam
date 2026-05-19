# TODO:
# input: PDF by url or file path
# output: structured text for proofreading

# API example: https://cloud.google.com/vision/docs/fulltext-annotations
# Return format: https://cloud.google.com/vision/docs/reference/rest/v1/images/annotate#TextAnnotation
# Billing: https://console.cloud.google.com/billing/

import logging
from dataclasses import dataclass
from pathlib import Path

from google.cloud import vision
from google.cloud.vision_v1 import AnnotateImageResponse


@dataclass
class OcrResponse:
    #: A slightly sanitized version of the OCR's plain-text output.
    text_content: str
    #: Word-level bounding boxes stored as 5-tuples (x1, x2, y1, y2, text).
    bounding_boxes: list[tuple[int, int, int, int, str]]


def post_process(text: str) -> str:
    """Post process OCR text."""
    return (
        text
        # Danda and double danda
        .replace("||", "॥")
        .replace("|", "।")
        .replace("।।", "॥")
        # Remove curly quotes
        .replace("‘", "'")
        .replace("’", "'")
        .replace("“", '"')
        .replace("”", '"')
    )


def prepare_image(file_path: Path):
    """Read an image into a protocol buffer for the OCR request."""
    with open(file_path, "rb") as file_path:
        content = file_path.read()
    return vision.Image(content=content)


def serialize_bounding_boxes(boxes: list[tuple[int, int, int, int, str]]) -> str:
    """Serialize a list of bounding boxes as a TSV."""
    return "\n".join("\t".join(str(x) for x in row) for row in boxes)


def debug_dump_response(response):
    """A handy debug function that dumps the OCR response to a JSON file."""
    with open("out.json", "w") as f:
        f.write(AnnotateImageResponse.to_json(response))


def run(file_path: Path, language: str = 'sa') -> OcrResponse:
    """Run Google OCR over the given image.

    :param file_path: path to the image we'll process with OCR.
    :param language: language code for OCR (default: 'sa' for Sanskrit).
    :return: an OCR response containing the image's text content and
        bounding boxes.
    """
    logging.debug(f"Starting full text annotation: {file_path} with language {language}")

    client = vision.ImageAnnotatorClient()
    image = prepare_image(file_path)

    # Set language hints based on the language parameter
    language_hints = []
    if language == 'sa':
        language_hints = ['sa']  # Sanskrit
    elif language == 'hi':
        language_hints = ['hi']  # Hindi
    elif language == 'te':
        language_hints = ['te']  # Telugu
    elif language == 'mr':
        language_hints = ['mr']  # Marathi
    elif language == 'bn':
        language_hints = ['bn']  # Bengali
    elif language == 'gu':
        language_hints = ['gu']  # Gujarati
    elif language == 'kn':
        language_hints = ['kn']  # Kannada
    elif language == 'ml':
        language_hints = ['ml']  # Malayalam
    elif language == 'ta':
        language_hints = ['ta']  # Tamil
    elif language == 'pa':
        language_hints = ['pa']  # Punjabi
    elif language == 'or':
        language_hints = ['or']  # Odia
    elif language == 'ur':
        language_hints = ['ur']  # Urdu
    else:
        language_hints = ['en']  # Default to English

    context = vision.ImageContext(language_hints=language_hints)
    response = client.document_text_detection(image=image, image_context=context)
    document = response.full_text_annotation

    buf = []
    bounding_boxes = []
    for page in document.pages:
        for block in page.blocks:
            for p in block.paragraphs:
                for w in p.words:
                    vertices = w.bounding_box.vertices
                    xs = [v.x for v in vertices]
                    ys = [v.y for v in vertices]
                    word = "".join(s.text for s in w.symbols)
                    bounding_boxes.append((min(xs), min(ys), max(xs), max(ys), word))

                    for s in w.symbols:
                        buf.append(s.text)
                        break_type = s.property.detected_break.type

                        # BreakType.SPACE
                        # BreakType.SURE_SPACE
                        # End of word.
                        if break_type in (1, 2):
                            buf.append(" ")

                        # BreakType.EOL_SURE_SPACE
                        # End of line.
                        if break_type == 3:
                            buf.append("\n")

                        # BreakType.HYPHEN:
                        # Hyphenated end-of-line.
                        elif break_type == 4:
                            buf.append("-\n")

                        # BreakType.LINE_BREAK
                        # Clean end of region.
                        elif break_type == 5:
                            buf.append("\n\n")

    text_content = post_process("".join(buf))
    return OcrResponse(text_content=text_content, bounding_boxes=bounding_boxes)


def run_with_selection(file_path: Path, selection: dict, language: str = 'sa') -> OcrResponse:
    """Run Google OCR on a specific selection of the image.

    :param file_path: path to the image we'll process with OCR.
    :param selection: dictionary with 'left', 'top', 'width', 'height' keys.
    :param language: language code for OCR (default: 'sa' for Sanskrit).
    :return: an OCR response containing the image's text content and
        bounding boxes.
    """
    logging.debug(f"Starting Google OCR on selection: {file_path} with language {language}")
    
    # Google OCR doesn't have built-in selection support, so we'll crop the image
    from PIL import Image
    image = Image.open(file_path)
    left, top, width, height = selection['left'], selection['top'], selection['width'], selection['height']
    selection_image = image.crop((left, top, left + width, top + height))
    
    # Save the cropped image temporarily
    import tempfile
    with tempfile.NamedTemporaryFile(suffix='.jpg', delete=False) as tmp_file:
        selection_image.save(tmp_file.name, 'JPEG')
        tmp_path = Path(tmp_file.name)
    
    try:
        return run(tmp_path, language=language)
    finally:
        # Clean up temporary file
        tmp_path.unlink(missing_ok=True)
