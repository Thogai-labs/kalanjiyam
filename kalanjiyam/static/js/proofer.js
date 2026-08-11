/* global Alpine, $, OpenSeadragon, Sanscript, IMAGE_URL */
/* Transcription and proofreading interface. */

import { $ } from './core.ts';
import {
  createRichEditor,
  getEditorContent,
  setEditorContent,
  setEditorText,
  destroyEditor,
  insertImage,
  insertTable,
  initializeToolbar,
} from './rich-editor.js';
import {
  parseDocument,
  documentToPlainText,
  documentToFlowHtml,
  hasStructuredBlocks,
  fromOcrPayload,
  parseBoundingBoxes,
  overlayBoxesFromPayload,
  boxesFromDocumentBlocks,
  reclusterDocumentBlocks,
  blocksFromFlowHtml,
} from './page-document.js';
import { OsdBboxOverlay, scaleBoxesToImage } from './osd-overlay.js';
import { ReplicaView } from './replica-view.js';

const CONFIG_KEY = 'proofing-editor';

const LAYOUT_SIDE_BY_SIDE = 'side-by-side';
const LAYOUT_TOP_AND_BOTTOM = 'top-and-bottom';
const ALL_LAYOUTS = [LAYOUT_SIDE_BY_SIDE, LAYOUT_TOP_AND_BOTTOM];

const CLASSES_SIDE_BY_SIDE = 'flex flex-col-reverse md:flex-row h-[90vh]';
const CLASSES_TOP_AND_BOTTOM = 'flex flex-col-reverse h-[90vh]';

/* Initialize our image viewer. */
function initializeImageViewer(imageURL) {
  return OpenSeadragon({
    id: 'osd-image',
    tileSources: {
      type: 'image',
      url: imageURL,
      buildPyramid: false,
    },

    // Buttons
    showZoomControl: false,
    showHomeControl: false,
    showRotationControl: false,
    showFullPageControl: false,
    // Zoom buttons are defined in the `Editor` component below.
    // Custom rotation buttons are defined in the template

    // Animations
    gestureSettingsMouse: {
      flickEnabled: true,
    },
    animationTime: 0.5,

    // The zoom multiplier to use when using the zoom in/out buttons.
    zoomPerClick: 1.1,
    // Max zoom level
    maxZoomPixelRatio: 2.5,
  });
}

export default () => ({
  // Settings
  textZoom: 1,
  imageZoom: null,
  layout: 'side-by-side',
  // [transliteration] the source script
  fromScript: 'hk',
  // [transliteration] the destination script
  toScript: 'devanagari',

  // Content
  content: '',

  // Editor mode: replica | flow
  editorMode: (typeof window.IS_DOCX !== 'undefined' && window.IS_DOCX) ? 'flow' : 'replica',
  isDocx: (typeof window.IS_DOCX !== 'undefined') ? window.IS_DOCX : false,
  showMetaPanel: false,
  activeVersion: (typeof ACTIVE_VERSION !== 'undefined') ? ACTIVE_VERSION : 'role:p1',
  targetVersion: (typeof TARGET_VERSION !== 'undefined') ? TARGET_VERSION : 'role:p1',
  availableVersions: (typeof AVAILABLE_VERSIONS !== 'undefined') ? AVAILABLE_VERSIONS : [],
  pageDocument: null,
  _flowPlainCache: '',
  _bboxOverlay: null,
  _replicaView: null,

  // OCR settings
  selectedEngine: '1', // Default to Google OCR (1)
  selectedLanguage: 'sa',

  // Translation settings
  selectedTranslationEngine: 'indictrans2',
  sourceLanguage: 'hi',
  targetLanguage: 'en',
  translationDropdownOpen: false,
  showTranslationInfo: false,
  allGlossaries: [],
  selectedGlossaries: [],

  // OCR dropdown state
  ocrDropdownOpen: false,
  showOcrEngineInfo: false,

  // Confidence review state
  uncertainCount: 0,
  _uncertainCursor: -1,

  // Conflict Resolver state
  showConflictResolver: (typeof window.HAS_CONFLICT !== 'undefined' && window.HAS_CONFLICT),
  conflictViewMode: 'split', // 'split' | 'diff' | 'manual'
  conflictContent: (typeof window.CONFLICT_CONTENT !== 'undefined') ? window.CONFLICT_CONTENT : '',
  yourContent: (typeof window.YOUR_CONTENT !== 'undefined') ? window.YOUR_CONTENT : '',
  conflictDiff: (typeof window.CONFLICT_DIFF !== 'undefined') ? window.CONFLICT_DIFF : '',
  conflictManualEditor: null,

  // Internal-only
  layoutClasses: CLASSES_SIDE_BY_SIDE,
  isRunningOCR: false,
  isRunningTranslation: false,
  hasUnsavedChanges: false,
  _isProgrammaticUpdate: false,
  imageViewer: null,
  richEditor: null,
  isDragging: false,
  toolbarUpdateTrigger: 0, // Used to trigger Alpine reactivity for toolbar updates
  _commandInProgress: false, // Flag to prevent callback interference during commands

  // OCR Engine configurations
  ocrEngines: {
    '1': {
      name: 'Google OCR',
      languages: [
        { value: 'sa', text: 'Sanskrit (sa)' },
        { value: 'en', text: 'English (en)' },
        { value: 'hi', text: 'Hindi (hi)' },
        { value: 'te', text: 'Telugu (te)' },
        { value: 'mr', text: 'Marathi (mr)' },
        { value: 'bn', text: 'Bengali (bn)' },
        { value: 'gu', text: 'Gujarati (gu)' },
        { value: 'kn', text: 'Kannada (kn)' },
        { value: 'ml', text: 'Malayalam (ml)' },
        { value: 'ta', text: 'Tamil (ta)' },
        { value: 'pa', text: 'Punjabi (pa)' },
        { value: 'or', text: 'Odia (or)' },
        { value: 'ur', text: 'Urdu (ur)' }
      ],
      supportsBilingual: false
    },
    '2': {
      name: 'Tesseract OCR',
      languages: [
        { value: 'san', text: 'Sanskrit (san)' },
        { value: 'eng', text: 'English (eng)' },
        { value: 'hin', text: 'Hindi (hin)' },
        { value: 'tel', text: 'Telugu (tel)' },
        { value: 'mar', text: 'Marathi (mar)' },
        { value: 'ben', text: 'Bengali (ben)' },
        { value: 'guj', text: 'Gujarati (guj)' },
        { value: 'kan', text: 'Kannada (kan)' },
        { value: 'mal', text: 'Malayalam (mal)' },
        { value: 'tam', text: 'Tamil (tam)' },
        { value: 'pan', text: 'Punjabi (pan)' },
        { value: 'ori', text: 'Odia (ori)' },
        { value: 'urd', text: 'Urdu (urd)' }
      ],
      supportsBilingual: true,
      bilingualSeparator: '+'
    },
    '3': {
      name: 'Surya OCR',
      languages: [
        { value: 'sa', text: 'Sanskrit (sa)' },
        { value: 'hi', text: 'Hindi (hi)' },
        { value: 'te', text: 'Telugu (te)' },
        { value: 'mr', text: 'Marathi (mr)' },
        { value: 'bn', text: 'Bengali (bn)' },
        { value: 'gu', text: 'Gujarati (gu)' },
        { value: 'kn', text: 'Kannada (kn)' },
        { value: 'ml', text: 'Malayalam (ml)' },
        { value: 'ta', text: 'Tamil (ta)' },
        { value: 'pa', text: 'Punjabi (pa)' },
        { value: 'or', text: 'Odia (or)' },
        { value: 'ur', text: 'Urdu (ur)' },
        { value: 'en', text: 'English (en)' },
        { value: 'ar', text: 'Arabic (ar)' },
        { value: 'fa', text: 'Persian (fa)' },
        { value: 'th', text: 'Thai (th)' },
        { value: 'ko', text: 'Korean (ko)' },
        { value: 'ja', text: 'Japanese (ja)' },
        { value: 'zh', text: 'Chinese (zh)' },
        { value: 'ru', text: 'Russian (ru)' },
        { value: 'es', text: 'Spanish (es)' },
        { value: 'fr', text: 'French (fr)' },
        { value: 'de', text: 'German (de)' },
        { value: 'it', text: 'Italian (it)' },
        { value: 'pt', text: 'Portuguese (pt)' },
        { value: 'nl', text: 'Dutch (nl)' },
        { value: 'pl', text: 'Polish (pl)' },
        { value: 'tr', text: 'Turkish (tr)' },
        { value: 'vi', text: 'Vietnamese (vi)' },
        { value: 'id', text: 'Indonesian (id)' },
        { value: 'ms', text: 'Malay (ms)' }
      ],
      supportsBilingual: true,
      bilingualSeparator: ',',
      autoDetect: true
    },
        '4': {
          name: 'Nanonets OCR',
          languages: [
            { value: 'sa', text: 'Sanskrit (sa)' },
            { value: 'en', text: 'English (en)' },
            { value: 'hi', text: 'Hindi (hi)' },
            { value: 'te', text: 'Telugu (te)' },
            { value: 'mr', text: 'Marathi (mr)' },
            { value: 'bn', text: 'Bengali (bn)' },
            { value: 'gu', text: 'Gujarati (gu)' },
            { value: 'kn', text: 'Kannada (kn)' },
            { value: 'ml', text: 'Malayalam (ml)' },
            { value: 'ta', text: 'Tamil (ta)' },
            { value: 'pa', text: 'Punjabi (pa)' },
            { value: 'or', text: 'Odia (or)' },
            { value: 'ur', text: 'Urdu (ur)' },
            { value: 'ar', text: 'Arabic (ar)' },
            { value: 'fa', text: 'Persian (fa)' },
            { value: 'th', text: 'Thai (th)' },
            { value: 'ko', text: 'Korean (ko)' },
            { value: 'ja', text: 'Japanese (ja)' },
            { value: 'zh', text: 'Chinese (zh)' },
            { value: 'ru', text: 'Russian (ru)' },
            { value: 'es', text: 'Spanish (es)' },
            { value: 'fr', text: 'French (fr)' },
            { value: 'de', text: 'German (de)' },
            { value: 'it', text: 'Italian (it)' },
            { value: 'pt', text: 'Portuguese (pt)' },
            { value: 'nl', text: 'Dutch (nl)' },
            { value: 'pl', text: 'Polish (pl)' },
            { value: 'tr', text: 'Turkish (tr)' },
            { value: 'vi', text: 'Vietnamese (vi)' },
            { value: 'id', text: 'Indonesian (id)' },
            { value: 'ms', text: 'Malay (ms)' }
          ],
          supportsBilingual: false
        },
        '5': {
          name: 'DeepSeek OCR',
          languages: [
            { value: 'sa', text: 'Sanskrit (sa)' },
            { value: 'en', text: 'English (en)' },
            { value: 'hi', text: 'Hindi (hi)' },
            { value: 'te', text: 'Telugu (te)' },
            { value: 'mr', text: 'Marathi (mr)' },
            { value: 'bn', text: 'Bengali (bn)' },
            { value: 'gu', text: 'Gujarati (gu)' },
            { value: 'kn', text: 'Kannada (kn)' },
            { value: 'ml', text: 'Malayalam (ml)' },
            { value: 'ta', text: 'Tamil (ta)' },
            { value: 'pa', text: 'Punjabi (pa)' },
            { value: 'or', text: 'Odia (or)' },
            { value: 'ur', text: 'Urdu (ur)' },
            { value: 'ar', text: 'Arabic (ar)' },
            { value: 'fa', text: 'Persian (fa)' },
            { value: 'th', text: 'Thai (th)' },
            { value: 'ko', text: 'Korean (ko)' },
            { value: 'ja', text: 'Japanese (ja)' },
            { value: 'zh', text: 'Chinese (zh)' },
            { value: 'ru', text: 'Russian (ru)' },
            { value: 'es', text: 'Spanish (es)' },
            { value: 'fr', text: 'French (fr)' },
            { value: 'de', text: 'German (de)' },
            { value: 'it', text: 'Italian (it)' },
            { value: 'pt', text: 'Portuguese (pt)' },
            { value: 'nl', text: 'Dutch (nl)' },
            { value: 'pl', text: 'Polish (pl)' },
            { value: 'tr', text: 'Turkish (tr)' },
            { value: 'vi', text: 'Vietnamese (vi)' },
            { value: 'id', text: 'Indonesian (id)' },
            { value: 'ms', text: 'Malay (ms)' }
          ],
          supportsBilingual: false
        },
         '6': {
           name: 'Chandra OCR',
           languages: [
             { value: 'sa', text: 'Sanskrit (sa)' },
             { value: 'en', text: 'English (en)' },
             { value: 'hi', text: 'Hindi (hi)' },
             { value: 'te', text: 'Telugu (te)' },
             { value: 'mr', text: 'Marathi (mr)' },
             { value: 'bn', text: 'Bengali (bn)' },
             { value: 'gu', text: 'Gujarati (gu)' },
             { value: 'kn', text: 'Kannada (kn)' },
             { value: 'ml', text: 'Malayalam (ml)' },
             { value: 'ta', text: 'Tamil (ta)' },
             { value: 'pa', text: 'Punjabi (pa)' },
             { value: 'or', text: 'Odia (or)' },
             { value: 'ur', text: 'Urdu (ur)' },
             { value: 'ar', text: 'Arabic (ar)' },
             { value: 'fa', text: 'Persian (fa)' },
             { value: 'th', text: 'Thai (th)' },
             { value: 'ko', text: 'Korean (ko)' },
             { value: 'ja', text: 'Japanese (ja)' },
             { value: 'zh', text: 'Chinese (zh)' },
             { value: 'ru', text: 'Russian (ru)' },
             { value: 'es', text: 'Spanish (es)' },
             { value: 'fr', text: 'French (fr)' },
             { value: 'de', text: 'German (de)' },
             { value: 'it', text: 'Italian (it)' },
             { value: 'pt', text: 'Portuguese (pt)' },
             { value: 'nl', text: 'Dutch (nl)' },
             { value: 'pl', text: 'Polish (pl)' },
             { value: 'tr', text: 'Turkish (tr)' },
             { value: 'vi', text: 'Vietnamese (vi)' },
             { value: 'id', text: 'Indonesian (id)' },
             { value: 'ms', text: 'Malay (ms)' },
             { value: 'zh-cn', text: 'Chinese Simplified (zh-cn)' },
             { value: 'zh-tw', text: 'Chinese Traditional (zh-tw)' },
             { value: 'tl', text: 'Filipino (tl)' }
           ],
           supportsBilingual: false
         },
         '7': {
           name: 'Qwen 2VL OCR',
           languages: [
             { value: 'sa', text: 'Sanskrit (sa)' },
             { value: 'en', text: 'English (en)' },
             { value: 'hi', text: 'Hindi (hi)' },
             { value: 'te', text: 'Telugu (te)' },
             { value: 'mr', text: 'Marathi (mr)' },
             { value: 'bn', text: 'Bengali (bn)' },
             { value: 'gu', text: 'Gujarati (gu)' },
             { value: 'kn', text: 'Kannada (kn)' },
             { value: 'ml', text: 'Malayalam (ml)' },
             { value: 'ta', text: 'Tamil (ta)' },
             { value: 'pa', text: 'Punjabi (pa)' },
             { value: 'or', text: 'Odia (or)' },
             { value: 'ur', text: 'Urdu (ur)' },
             { value: 'ar', text: 'Arabic (ar)' },
             { value: 'fa', text: 'Persian (fa)' },
             { value: 'th', text: 'Thai (th)' },
             { value: 'ko', text: 'Korean (ko)' },
             { value: 'ja', text: 'Japanese (ja)' },
             { value: 'zh', text: 'Chinese (zh)' },
             { value: 'ru', text: 'Russian (ru)' },
             { value: 'es', text: 'Spanish (es)' },
             { value: 'fr', text: 'French (fr)' },
             { value: 'de', text: 'German (de)' },
             { value: 'it', text: 'Italian (it)' },
             { value: 'pt', text: 'Portuguese (pt)' },
             { value: 'nl', text: 'Dutch (nl)' },
             { value: 'pl', text: 'Polish (pl)' },
             { value: 'tr', text: 'Turkish (tr)' },
             { value: 'vi', text: 'Vietnamese (vi)' },
             { value: 'id', text: 'Indonesian (id)' },
             { value: 'ms', text: 'Malay (ms)' },
             { value: 'zh-cn', text: 'Chinese Simplified (zh-cn)' },
             { value: 'zh-tw', text: 'Chinese Traditional (zh-tw)' },
             { value: 'tl', text: 'Filipino (tl)' },
             { value: 'my', text: 'Myanmar (my)' },
             { value: 'km', text: 'Khmer (km)' },
             { value: 'lo', text: 'Lao (lo)' },
             { value: 'ne', text: 'Nepali (ne)' },
             { value: 'si', text: 'Sinhala (si)' },
             { value: 'dz', text: 'Dzongkha (dz)' },
             { value: 'bo', text: 'Tibetan (bo)' },
             { value: 'ug', text: 'Uyghur (ug)' },
             { value: 'mn', text: 'Mongolian (mn)' },
             { value: 'kk', text: 'Kazakh (kk)' },
             { value: 'ky', text: 'Kyrgyz (ky)' },
             { value: 'uz', text: 'Uzbek (uz)' },
             { value: 'tg', text: 'Tajik (tg)' },
             { value: 'az', text: 'Azerbaijani (az)' },
             { value: 'tk', text: 'Turkmen (tk)' },
             { value: 'ka', text: 'Georgian (ka)' },
             { value: 'hy', text: 'Armenian (hy)' },
             { value: 'am', text: 'Amharic (am)' },
             { value: 'ti', text: 'Tigrinya (ti)' },
             { value: 'om', text: 'Oromo (om)' },
             { value: 'so', text: 'Somali (so)' },
             { value: 'sw', text: 'Swahili (sw)' },
             { value: 'zu', text: 'Zulu (zu)' },
             { value: 'xh', text: 'Xhosa (xh)' },
             { value: 'af', text: 'Afrikaans (af)' },
             { value: 'sq', text: 'Albanian (sq)' },
             { value: 'eu', text: 'Basque (eu)' },
             { value: 'be', text: 'Belarusian (be)' },
             { value: 'bg', text: 'Bulgarian (bg)' },
             { value: 'hr', text: 'Croatian (hr)' },
             { value: 'cs', text: 'Czech (cs)' },
             { value: 'da', text: 'Danish (da)' },
             { value: 'et', text: 'Estonian (et)' },
             { value: 'fi', text: 'Finnish (fi)' },
             { value: 'gl', text: 'Galician (gl)' },
             { value: 'hu', text: 'Hungarian (hu)' },
             { value: 'is', text: 'Icelandic (is)' },
             { value: 'ga', text: 'Irish (ga)' },
             { value: 'lv', text: 'Latvian (lv)' },
             { value: 'lt', text: 'Lithuanian (lt)' },
             { value: 'mk', text: 'Macedonian (mk)' },
             { value: 'mt', text: 'Maltese (mt)' },
             { value: 'no', text: 'Norwegian (no)' },
             { value: 'ro', text: 'Romanian (ro)' },
             { value: 'sk', text: 'Slovak (sk)' },
             { value: 'sl', text: 'Slovenian (sl)' },
             { value: 'sv', text: 'Swedish (sv)' },
             { value: 'uk', text: 'Ukrainian (uk)' },
             { value: 'cy', text: 'Welsh (cy)' },
             { value: 'he', text: 'Hebrew (he)' },
             { value: 'yi', text: 'Yiddish (yi)' },
             { value: 'jv', text: 'Javanese (jv)' },
             { value: 'su', text: 'Sundanese (su)' },
             { value: 'ceb', text: 'Cebuano (ceb)' },
             { value: 'haw', text: 'Hawaiian (haw)' },
             { value: 'mg', text: 'Malagasy (mg)' },
             { value: 'mi', text: 'Maori (mi)' },
             { value: 'sm', text: 'Samoan (sm)' },
             { value: 'to', text: 'Tongan (to)' },
             { value: 'ty', text: 'Tahitian (ty)' },
             { value: 've', text: 'Venda (ve)' },
             { value: 'wo', text: 'Wolof (wo)' },
             { value: 'yo', text: 'Yoruba (yo)' }
           ],
           supportsBilingual: false
         },
         '9': {
           name: 'Paddle Table OCR',
           languages: [
             { value: 'sa', text: 'Sanskrit (sa)' },
             { value: 'en', text: 'English (en)' },
             { value: 'hi', text: 'Hindi (hi)' },
             { value: 'te', text: 'Telugu (te)' },
             { value: 'mr', text: 'Marathi (mr)' },
             { value: 'bn', text: 'Bengali (bn)' },
             { value: 'gu', text: 'Gujarati (gu)' },
             { value: 'kn', text: 'Kannada (kn)' },
             { value: 'ml', text: 'Malayalam (ml)' },
             { value: 'ta', text: 'Tamil (ta)' },
             { value: 'pa', text: 'Punjabi (pa)' },
             { value: 'or', text: 'Odia (or)' },
             { value: 'ur', text: 'Urdu (ur)' },
             { value: 'zh', text: 'Chinese (zh)' },
             { value: 'ja', text: 'Japanese (ja)' },
             { value: 'ko', text: 'Korean (ko)' },
             { value: 'ar', text: 'Arabic (ar)' },
             { value: 'fr', text: 'French (fr)' },
             { value: 'de', text: 'German (de)' },
             { value: 'es', text: 'Spanish (es)' },
             { value: 'pt', text: 'Portuguese (pt)' },
             { value: 'ru', text: 'Russian (ru)' },
             { value: 'it', text: 'Italian (it)' }
           ],
           supportsBilingual: false
         },
         '11': {
           name: 'Sanskrit Manuscript OCR',
           languages: [
             { value: 'san', text: 'Sanskrit (san)' }
           ],
           supportsBilingual: false
         }
  },



  toolbarCommand(command, options) {
    const editor = window.richEditorInstance;
    if (!editor) return;

    if (command === 'insertTable') {
      if (options && options.rows && options.cols) {
        insertTable(editor, options.rows, options.cols);
      }
    }
  },

  init() {
    this.isDocx = (typeof window.IS_DOCX !== 'undefined') ? window.IS_DOCX : false;
    if (this.isDocx) {
      this.editorMode = 'flow';
    }
    this.loadSettings();
    if (this.isDocx) {
      this.editorMode = 'flow';
    }
    this.layoutClasses = this.getLayoutClasses();

    // For DOCX projects, eagerly parse pageDocument *before* the flow editor
    // so that _flowHtmlFromDocument() returns the real HTML, not empty/plain.
    if (this.isDocx) {
      const raw = typeof PAGE_DOCUMENT_JSON !== 'undefined' ? PAGE_DOCUMENT_JSON : null;
      if (raw) {
        this.pageDocument = parseDocument(raw);
        // Do NOT recluster DOCX blocks — they are a single HTML blob,
        // not OCR word-level boxes.
      }
      const key = this._getStorageKey();
      if (key) {
        const cached = localStorage.getItem(key);
        if (cached) {
          try {
            const parsed = JSON.parse(cached);
            if (parsed && parsed.blocks && parsed.blocks.length > 0) {
              this.pageDocument = parsed;
              this.hasUnsavedChanges = true;
            }
          } catch (e) {
            console.error('Error loading cached document from localStorage:', e);
          }
        }
      }
    }

    // Initialize content from the textarea if it exists
    const textarea = document.getElementById('content');
    if (textarea && textarea.value) {
      this.content = textarea.value;
      this._flowPlainCache = textarea.value;
    }

    // Set `imageViewer` only if not a DOCX project
    if (!this.isDocx) {
      this.imageViewer = initializeImageViewer(IMAGE_URL);
      this.imageViewer.addHandler('open', () => {
        this.imageZoom = this.imageZoom || this.imageViewer.viewport.getHomeZoom();
        this.imageViewer.viewport.zoomTo(this.imageZoom);
      });
    }

    // Use `.bind(this)` so that `this` in the function refers to this app and
    // not `window`.
    window.onbeforeunload = this.onBeforeUnload.bind(this);
    
    // Initialize language options
    this.updateLanguageOptions();
    
    // Add event listeners for rotation buttons
    if (!this.isDocx) {
      this.setupRotationButtons();
    }
    
    // Initialize translation selector
    this.initTranslationSelector();
    
    // Flow editor is lazy-init when the pane becomes visible (TipTap breaks in display:none).
    this.initPageDocumentEditor();
    this.checkVersionAndCacheOnLoad();
    if (this.editorMode === 'flow') {
      setTimeout(() => this.ensureFlowEditor(), 0);
    }
    if (!this.isDocx) {
      this.setupZoomButtons();
    }
  },

  getVersionDisplayName(versionKey) {
    if (!versionKey) return '';
    const found = this.availableVersions.find(v => v.version_key === versionKey);
    if (found) return found.display_name;

    if (versionKey === 'main') return 'Main Branch';
    if (versionKey === 'role:p1') return 'Legacy Consolidated P1';
    if (versionKey === 'role:p2') return 'Legacy Consolidated P2';
    if (versionKey === 'role:moderator') return 'Legacy Consolidated Moderator';
    if (versionKey.startsWith('ocr:')) {
      const engine = versionKey.split(':')[1];
      const engineMap = {
        "google": "1",
        "tesseract": "2",
        "surya": "3",
        "nanonets": "4",
        "deepseek": "5",
        "chandra": "6",
        "qwen3": "7",
        "surya_table": "8",
        "paddle_table": "9",
        "glm_ocr": "10",
        "tesseract_manuscript": "11",
        "dots_ocr": "12"
      };
      const num = engineMap[engine] || engine;
      if (/^\d+$/.test(num)) {
        return 'OCR ' + num;
      }
      return num.charAt(0).toUpperCase() + num.slice(1) + ' OCR';
    }
    return versionKey;
  },

  switchVersion(versionKey) {
    if (this.hasUnsavedChanges && !confirm('You have unsaved changes. Are you sure you want to switch versions and discard changes?')) {
      return;
    }
    const url = new URL(window.location.href);
    url.searchParams.set('version', versionKey);
    window.location.href = url.toString();
  },

  deriveFromVersion(versionKey) {
    const display = this.getVersionDisplayName(versionKey);
    const targetDisplay = this.getVersionDisplayName(this.targetVersion);
    if (!confirm(`Are you sure you want to load "${display}" content into your active editing track "${targetDisplay}"? Your current unsaved edits will be replaced.`)) {
      return;
    }
    const url = new URL(window.location.href);
    url.searchParams.set('version', versionKey);
    window.location.href = url.toString();
  },

  ensureFlowEditor() {
    if (window.richEditorInstance) return window.richEditorInstance;

    const editorElement = document.getElementById('rich-editor');
    if (!editorElement) {
      console.warn('ensureFlowEditor: #rich-editor element not found in DOM');
      return null;
    }

    const textarea = document.getElementById('content');
    let initialHtml = this._flowHtmlFromDocument();
    if (!initialHtml) {
      const plain = this._flowPlainCache
        || (textarea ? textarea.value : '')
        || this.content;
      if (plain) {
        initialHtml = plain
          .split('\n\n')
          .map((p) => `<p>${p.replace(/\n/g, '<br>')}</p>`)
          .join('');
      }
    }

    this._isProgrammaticUpdate = true;
    const editor = createRichEditor('rich-editor', {
      content: initialHtml || '',
      onUploadImage: (file) => {
        this.uploadImageFiles([file]);
      },
      onUpdate: (html) => {
        this.content = html;
        const contentTextarea = document.getElementById('content');
        if (contentTextarea) contentTextarea.value = html;
        // Rebuild pageDocument blocks from flow HTML so replica stays in sync.
        // Pass the current blocks so ids/geometry/provenance survive the trip.
        const newBlocks = blocksFromFlowHtml(html, this.pageDocument?.blocks || [], this.pageDocument?.content_format || 'blocks');
        if (newBlocks.length && this.pageDocument) {
          this.pageDocument = { ...this.pageDocument, blocks: newBlocks };
          const docField = document.getElementById('document');
          if (docField) docField.value = JSON.stringify(this.pageDocument);
          this._updateUncertainCount();
        }
        if (this._isProgrammaticUpdate) {
          this._isProgrammaticUpdate = false;
        } else {
          this.hasUnsavedChanges = true;
          if (this._replicaView) {
            this._replicaView.document = this.pageDocument;
            this._replicaView.triggerChange();
          } else {
            const key = this._getStorageKey();
            if (key && this.pageDocument) {
              const serverVer = (typeof window.PAGE_VERSION !== 'undefined') ? parseInt(window.PAGE_VERSION, 10) : 0;
              const payload = {
                version: serverVer,
                document: this.pageDocument,
                content: html,
                timestamp: Date.now()
              };
              localStorage.setItem(key, JSON.stringify(payload));
            }
          }
        }
      },
      onSelectionUpdate: () => {},
    });
    this._isProgrammaticUpdate = false;

    window.richEditorInstance = editor;
    this._richEditor = editor;
    this.richEditor = true;

    if (editor) {
      initializeToolbar(editor);
    }

    this.syncEditorFromTextarea = () => {
      const contentTextarea = document.getElementById('content');
      if (contentTextarea && window.richEditorInstance && contentTextarea.value) {
        try {
          this._isProgrammaticUpdate = true;
          setEditorText(window.richEditorInstance, contentTextarea.value);
          this._isProgrammaticUpdate = false;
        } catch (e) {
          console.error('Manual sync failed:', e);
        }
      }
    };

    window.uploadImageFiles = this.uploadImageFiles.bind(this);
    return editor;
  },

  _applyFlowEditorContent() {
    const plain = this._flowPlainCache
      || (this.pageDocument ? documentToPlainText(this.pageDocument) : '');
    const textarea = document.getElementById('content');
    if (textarea) {
      textarea.value = plain;
    }
    this.content = plain;
    const editor = this.ensureFlowEditor();
    if (!editor) {
      console.warn('_applyFlowEditorContent: ensureFlowEditor returned null');
      return;
    }
    const html = this._flowHtmlFromDocument();
    const currentContent = getEditorContent(editor);
    if (html) {
      if (currentContent !== html) {
        this._isProgrammaticUpdate = true;
        setEditorContent(editor, html);
        this._isProgrammaticUpdate = false;
      }
    } else {
      if (currentContent !== '<p></p>' && currentContent !== '') {
        this._isProgrammaticUpdate = true;
        setEditorContent(editor, '');
        this._isProgrammaticUpdate = false;
      }
    }
    // Force focus and layout update when switching to flow mode
    setTimeout(() => {
      try {
        editor.commands.focus();
      } catch (e) {
        console.warn('Failed to focus editor:', e);
      }
    }, 50);
  },

  setEditorMode(mode) {
    this.editorMode = mode;
    this.saveSettings();
    requestAnimationFrame(() => {
      if (this.imageViewer) {
        try {
          if (this.imageViewer.viewport) {
            this.imageViewer.viewport.resize();
            this.imageViewer.forceRedraw();
          }
        } catch (e) {
          console.warn('Failed to resize OpenSeadragon viewport:', e);
        }
        if (this._bboxOverlay) {
          this._bboxOverlay.setBoxes(this._bboxOverlay.boxes || []);
        }
      }
      if (this._replicaView && this.pageDocument) {
        this._replicaView.setDocument(this.pageDocument);
      }
      if (mode === 'flow') {
        setTimeout(() => {
          this._applyFlowEditorContent();
        }, 100);
      }
    });
  },

  _applyPageDimensionsFromImage() {
    if (typeof IMAGE_URL === 'undefined' || !IMAGE_URL) return;
    if (this.pageDocument.page_width && this.pageDocument.page_height) return;
    const img = new Image();
    img.onload = () => {
      if (!this.pageDocument.page_width && img.naturalWidth) {
        this.pageDocument.page_width = img.naturalWidth;
      }
      if (!this.pageDocument.page_height && img.naturalHeight) {
        this.pageDocument.page_height = img.naturalHeight;
      }
      if (this._replicaView) {
        this._replicaView.setDocument(this.pageDocument);
      }
      this._setOverlayBoxes(this.pageDocument);
      this._syncDocumentToForm();
    };
    img.src = IMAGE_URL;
  },

  _setOverlayBoxes(source) {
    if (!this._bboxOverlay) return;
    let boxes = boxesFromDocumentBlocks(this.pageDocument?.blocks);
    if (!boxes.length) {
      if (source && (source.bounding_boxes || source.blocks)) {
        boxes = overlayBoxesFromPayload(source, this.pageDocument);
      } else if (typeof source === 'string') {
        boxes = parseBoundingBoxes(source);
      }
    }
    this._bboxOverlay.setBoxes(boxes);
    this._bboxOverlay.setBlocksForMatching(this.pageDocument?.blocks || []);
  },

  initPageDocumentEditor() {
    setTimeout(() => {
      // For DOCX projects, pageDocument was already parsed eagerly in init().
      // Skip re-parsing and the destructive _syncDocumentToForm that blanks
      // the flow editor.
      if (this.isDocx && this.pageDocument) {
        // Just sync the hidden form field so Publish works.
        const docField = document.getElementById('document');
        if (docField) docField.value = JSON.stringify(this.pageDocument);
        return;
      }

      const raw = typeof PAGE_DOCUMENT_JSON !== 'undefined' ? PAGE_DOCUMENT_JSON : null;
      this.pageDocument = reclusterDocumentBlocks(parseDocument(raw));
      if (typeof PAGE_WIDTH !== 'undefined' && PAGE_WIDTH && !this.pageDocument.page_width) {
        this.pageDocument.page_width = PAGE_WIDTH;
      }
      if (typeof PAGE_HEIGHT !== 'undefined' && PAGE_HEIGHT && !this.pageDocument.page_height) {
        this.pageDocument.page_height = PAGE_HEIGHT;
      }
      this._applyPageDimensionsFromImage();
      const replicaRoot = document.getElementById('ocr-replica-root');
      if (replicaRoot && !this.isDocx) {
        this._replicaView = new ReplicaView(replicaRoot, {
          onChange: (doc) => {
            this.pageDocument = doc;
            this._syncDocumentToForm();
            if (this._replicaView && !this._replicaView.isRestoredFromCache && JSON.stringify(doc) === JSON.stringify(this._replicaView.originalDocument)) {
              this.hasUnsavedChanges = false;
            } else {
              this.hasUnsavedChanges = true;
            }
            if (this.editorMode === 'flow' && !this._isProgrammaticUpdate) {
              this._applyFlowEditorContent();
            }
          },
          onSelect: (block) => {
            if (this._bboxOverlay) this._bboxOverlay.highlightBlockId(block.id);
          },
          onTableFocus: (block) => {
            this.setEditorMode('flow');
            setTimeout(() => {
              const el = document.querySelector(`[data-block-id="${block.id}"]`);
              if (el) el.scrollIntoView({ behavior: 'smooth', block: 'center' });
            }, 150);
          },
        });
        this._replicaView.setDocument(this.pageDocument);
      }
      if (this.imageViewer) {
        this._bboxOverlay = new OsdBboxOverlay(this.imageViewer, {
          onBoxClick: ({ block }) => {
            if (block && this._replicaView) {
              this._replicaView.highlightBlock(block.id);
            }
          },
        });
        this._setOverlayBoxes(this.pageDocument);
      }
      this._syncDocumentToForm();
    }, 200);
  },

  /* Blocks the model was unsure about and no human has reviewed yet. */
  _uncertainBlocks() {
    return (this.pageDocument?.blocks || [])
      .filter((b) => b.confidence != null && b.confidence < 0.75 && !b.manually_edited)
      .sort((a, b) => (a.reading_order || 0) - (b.reading_order || 0));
  },

  _updateUncertainCount() {
    this.uncertainCount = this._uncertainBlocks().length;
  },

  /* Cycle through low-confidence blocks: highlight on the scan and in the
   * editor pane (replica or flow). */
  jumpToNextUncertain() {
    const uncertain = this._uncertainBlocks();
    if (!uncertain.length) return;
    this._uncertainCursor = (this._uncertainCursor + 1) % uncertain.length;
    const block = uncertain[this._uncertainCursor];
    if (this._bboxOverlay) this._bboxOverlay.highlightBlockId(block.id);
    if (this.editorMode === 'replica' && this._replicaView) {
      this._replicaView.focusBlock(block.id);
    } else {
      const el = document.querySelector(`#rich-editor [data-block-id="${block.id}"]`);
      if (el) el.scrollIntoView({ behavior: 'smooth', block: 'center' });
    }
  },

  _syncDocumentToForm() {
    if (!this.pageDocument) return;
    this._updateUncertainCount();

    // For html-format documents (DOCX), documentToPlainText returns the raw
    // HTML string.  Writing that into the textarea is fine (it is the
    // canonical content), but we must NOT feed it back into
    // _applyFlowEditorContent because that would cause TipTap to re-parse
    // and normalize the HTML, losing content on every round-trip.
    const isHtmlFormat = this.pageDocument.content_format === 'html';

    const plain = documentToPlainText(this.pageDocument);
    this._flowPlainCache = plain;

    const docField = document.getElementById('document');
    const textarea = document.getElementById('content');
    if (docField) {
      docField.value = JSON.stringify(this.pageDocument);
    }
    if (textarea) {
      textarea.value = plain;
      this.content = plain;
    }
    // Skip _applyFlowEditorContent for DOCX/html-format documents:
    // the flow editor is authoritative and should not be overwritten.
    if (this.editorMode === 'flow' && !isHtmlFormat) {
      this._applyFlowEditorContent();
    }
  },

  _flowHtmlFromDocument() {
    if (!this.pageDocument) return '';
    // Always render via documentToFlowHtml: it carries data-block-id, which
    // lets flow edits round-trip back onto the same blocks (geometry,
    // confidence, provenance intact).
    if (this.pageDocument.blocks?.length) {
      return documentToFlowHtml(this.pageDocument);
    }
    const plain = this._flowPlainCache
      || documentToPlainText(this.pageDocument);
    if (!plain) return '';
    return plain
      .split('\n\n')
      .map((p) => `<p>${p.replace(/\n/g, '<br>')}</p>`)
      .join('');
  },

  applyOcrPayload(payload) {
    const editedCount = (this.pageDocument?.blocks || []).filter((b) => b.manually_edited).length;
    if (editedCount > 0) {
      this.showNotification(
        `${editedCount} manually-edited block${editedCount > 1 ? 's' : ''} replaced by new OCR run.`,
        'warning',
      );
    }
    this.pageDocument = reclusterDocumentBlocks(fromOcrPayload(payload));
    if (payload.page_width) this.pageDocument.page_width = payload.page_width;
    if (payload.page_height) this.pageDocument.page_height = payload.page_height;
    this._flowPlainCache = documentToPlainText(this.pageDocument);
    if (this._replicaView) this._replicaView.setDocument(this.pageDocument);
    if (this._bboxOverlay) {
      this._setOverlayBoxes(payload);
      requestAnimationFrame(() => {
        if (this._bboxOverlay) this._setOverlayBoxes(payload);
      });
    }
    this._syncDocumentToForm();
    this.hasUnsavedChanges = false;
  },

  setupZoomButtons() {
    const zoomIn = document.getElementById('osd-zoom-in');
    const zoomOut = document.getElementById('osd-zoom-out');
    const zoomReset = document.getElementById('osd-home');
    if (zoomIn) zoomIn.addEventListener('click', (e) => { e.preventDefault(); this.increaseImageZoom(); });
    if (zoomOut) zoomOut.addEventListener('click', (e) => { e.preventDefault(); this.decreaseImageZoom(); });
    if (zoomReset) zoomReset.addEventListener('click', (e) => { e.preventDefault(); this.resetImageZoom(); });
  },

  // Drag and drop handlers for image upload
  handleDragOver(e) {
    e.preventDefault();
    this.isDragging = true;
  },
  
  handleDragLeave() {
    this.isDragging = false;
  },
  
  handleDrop(e) {
    e.preventDefault();
    this.isDragging = false;
    const files = e.dataTransfer.files;
    if (files.length > 0 && files[0].type.startsWith('image/')) {
      this.uploadImageFiles(Array.from(files));
    }
  },

  
  // Upload image files
  async uploadImageFiles(files) {
    if (!files || files.length === 0) return;
    
    // Get project and page slugs from URL
    const pathMatch = window.location.pathname.match(/\/proofing\/([^\/]+)\/([^\/]+)/);
    if (!pathMatch) {
      this.showNotification('Could not determine project/page from URL', 'error');
      return;
    }
    
    const [, projectSlug, pageSlug] = pathMatch;
    
    // Upload each file
    for (const file of files) {
      if (!file.type.startsWith('image/')) {
        this.showNotification(`File ${file.name} is not an image`, 'error');
        continue;
      }
      
      try {
        await this.uploadImage(file, projectSlug, pageSlug);
      } catch (error) {
        console.error('Image upload error:', error);
        this.showNotification(`Failed to upload ${file.name}: ${error.message}`, 'error');
      }
    }
  },
  
  // Handle image upload from file input
  handleImageUpload(event) {
    const files = event.target.files;
    if (files && files.length > 0) {
      this.uploadImageFiles(Array.from(files));
    }
    // Reset input so same file can be selected again
    event.target.value = '';
  },
  
  // Upload a single image
  async uploadImage(file, projectSlug, pageSlug) {
    const formData = new FormData();
    formData.append('image', file);
    
    // Construct URL correctly - use the same pattern as OCR route
    const { pathname } = window.location;
    const url = pathname.replace('/proofing/', '/api/upload-image/');
    
    try {
      const response = await fetch(url, {
        method: 'POST',
        body: formData,
      });
      
      if (!response.ok) {
        const errorText = await this.getErrorMessage(response);
        throw new Error(errorText || `Upload failed with status ${response.status}`);
      }
      
      const result = await response.json();
      
      if (result.success && result.url) {
        // Insert image into editor
        const editor = window.richEditorInstance;
        if (editor && editor.isEditable) {
          // Use setTimeout with a small delay to ensure editor is stable
          // This gives any pending updates time to complete
          setTimeout(() => {
            try {
              // Ensure editor is still ready
              if (!editor || !editor.isEditable) {
                this.showNotification('Editor became unavailable', 'error');
                return;
              }
              
              console.log('Editor state before insertion:', {
                isEditable: editor.isEditable,
                docSize: editor.state.doc.content.size,
                selection: editor.state.selection,
                hasImageNode: !!editor.state.schema.nodes.image
              });
              
              // Insert image - function will use current state at insertion time
              // insertImage might return a Promise now
              const insertResult = insertImage(editor, result.url);
              
              if (insertResult instanceof Promise) {
                insertResult.then((success) => {
                  if (success) {
                    this.showNotification(`Image ${result.filename} uploaded successfully`, 'success');
                  } else {
                    console.error('Image insertion failed');
                    this.showNotification(`Image uploaded but failed to insert. Check browser console.`, 'error');
                  }
                });
              } else if (insertResult === true) {
                this.showNotification(`Image ${result.filename} uploaded successfully`, 'success');
              } else {
                console.error('Image insertion failed');
                this.showNotification(`Image uploaded but failed to insert. Check browser console.`, 'error');
              }
            } catch (error) {
              console.error('Error in image insertion callback:', error);
              this.showNotification(`Error inserting image: ${error.message}`, 'error');
            }
          }, 100); // Small delay to ensure editor is stable
        } else {
          this.showNotification('Editor not initialized or not editable', 'error');
        }
      } else {
        throw new Error('Upload response missing URL');
      }
    } catch (error) {
      console.error('Image upload error:', error);
      throw error;
    }
  },

  // Settings IO

  loadSettings() {
    const settingsStr = localStorage.getItem(CONFIG_KEY);
    if (settingsStr) {
      try {
        const settings = JSON.parse(settingsStr);
        this.textZoom = settings.textZoom || this.textZoom;
        // We can only get an accurate default zoom after the viewer is fully
        // initialized. See `init` for details.
        this.imageZoom = settings.imageZoom;
        this.layout = settings.layout || this.layout;
        let mode = settings.editorMode || this.editorMode;
        if (mode === 'split') mode = 'replica';
        this.editorMode = mode;

        this.fromScript = settings.fromScript || this.fromScript;
        this.toScript = settings.toScript || this.toScript;
        
        // Load OCR settings
        this.selectedEngine = settings.selectedEngine || this.selectedEngine;
        this.selectedLanguage = settings.selectedLanguage || this.selectedLanguage;
        
        // Load Translation settings
        this.selectedTranslationEngine = settings.selectedTranslationEngine || this.selectedTranslationEngine;
        this.sourceLanguage = settings.sourceLanguage || this.sourceLanguage;
        this.targetLanguage = settings.targetLanguage || this.targetLanguage;
      } catch (error) {
        // Old settings are invalid -- rewrite with valid values.
        this.saveSettings();
      }
    }
  },
  saveSettings() {
    const settings = {
      textZoom: this.textZoom,
      imageZoom: this.imageZoom,
      layout: this.layout,
      editorMode: this.editorMode,
      fromScript: this.fromScript,
      toScript: this.toScript,
      selectedEngine: this.selectedEngine,
      selectedLanguage: this.selectedLanguage,
      selectedTranslationEngine: this.selectedTranslationEngine,
      sourceLanguage: this.sourceLanguage,
      targetLanguage: this.targetLanguage,
    };
    localStorage.setItem(CONFIG_KEY, JSON.stringify(settings));
  },
  getLayoutClasses() {
    if (this.layout === LAYOUT_TOP_AND_BOTTOM) {
      return CLASSES_TOP_AND_BOTTOM;
    }
    return CLASSES_SIDE_BY_SIDE;
  },

  // Callbacks

  /** Displays a warning dialog if the user has unsaved changes and tries to navigate away. */
  onBeforeUnload(e) {
    if (this.hasUnsavedChanges) {
      // Keeps the dialog event.
      return true;
    }
    // Cancels the dialog event.
    return null;
  },

  // OCR controls

  selectOcrEngine(engineValue, save = false) {
    this.selectedEngine = engineValue;
    window._ocrSelectedEngine = engineValue;
    this.updateLanguageOptions();
    if (save) {
      this.saveSettings();
    }
  },

  updateLanguageOptions() {
    // Small delay to ensure DOM is updated
    setTimeout(() => {
      const engine = this.selectedEngine;
      const engineConfig = this.ocrEngines[engine];
      const languageSelect = document.getElementById('language-select');
      const additionalLanguageSelect = document.getElementById('additional-language-select');

      if (!languageSelect) {
        return;
      }

      const languageContainer = languageSelect.closest('li, div.dropdown-item-no-hover, div');
      const hasLanguages = engineConfig && Array.isArray(engineConfig.languages) && engineConfig.languages.length > 0;

      if (!hasLanguages) {
        // Hide language selection UI if engine detects languages on the fly or provides no language list
        if (languageContainer) {
          languageContainer.style.display = 'none';
        }
        languageSelect.innerHTML = '';
        if (additionalLanguageSelect) {
          const addContainer = additionalLanguageSelect.closest('li, div.dropdown-item-no-hover, div');
          if (addContainer) addContainer.style.display = 'none';
        }
        return;
      }

      // Restore visibility when engine has language options
      if (languageContainer) {
        languageContainer.style.display = '';
      }

      // Clear existing options
      languageSelect.innerHTML = '';

      // Add language options
      engineConfig.languages.forEach(lang => {
        const option = document.createElement('option');
        option.value = lang.value;
        option.textContent = lang.text;
        languageSelect.appendChild(option);
      });

      // Set default language if current selection is not available
      if (!engineConfig.languages.find(lang => lang.value === this.selectedLanguage)) {
        const languageMap = {
          'sa': 'san',  // Google Sanskrit -> Tesseract Sanskrit
          'san': 'sa',  // Tesseract Sanskrit -> Google Sanskrit
          'en': 'eng',  // Google English -> Tesseract English
          'eng': 'en',  // Tesseract English -> Google English
          'hi': 'hin',  // Google Hindi -> Tesseract Hindi
          'hin': 'hi',  // Tesseract Hindi -> Google Hindi
        };

        const mappedLanguage = languageMap[this.selectedLanguage];
        if (mappedLanguage && engineConfig.languages.find(lang => lang.value === mappedLanguage)) {
          this.selectedLanguage = mappedLanguage;
        } else {
          this.selectedLanguage = engineConfig.languages[0].value;
        }
      }

      // Update additional language options for bilingual support
      if (additionalLanguageSelect) {
        const addContainer = additionalLanguageSelect.closest('li, div.dropdown-item-no-hover, div');
        if (engine === '2' || engine === '3') {
          if (addContainer) addContainer.style.display = '';
          additionalLanguageSelect.innerHTML = '<option value="">None</option>';
          engineConfig.languages.forEach(lang => {
            const option = document.createElement('option');
            option.value = lang.value;
            option.textContent = lang.text;
            additionalLanguageSelect.appendChild(option);
          });
        } else {
          if (addContainer) addContainer.style.display = 'none';
        }
      }
    }, 0);
  },

  // Decode numeric engine values to actual engine names
  decodeEngine(engineValue) {
    const engineMap = {
      '1': 'google',
      '2': 'tesseract',
      '3': 'surya',
      '4': 'nanonets',
      '5': 'deepseek',
      '6': 'chandra',
      '7': 'qwen3',
      '8': 'surya_table',
      '9': 'paddle_table',
      '10': 'glm_ocr',
      '11': 'tesseract_manuscript',
      '12': 'dots_ocr',
    };
    return engineMap[engineValue] || 'google';
  },

  // Get combined language parameter for bilingual support
  getCombinedLanguage() {
    const engine = this.selectedEngine;
    const primaryLanguage = this.selectedLanguage;
    const additionalLanguageSelect = document.getElementById('additional-language-select');
    const additionalLanguage = additionalLanguageSelect ? additionalLanguageSelect.value : '';
    
    if (engine === '2' && additionalLanguage) {
      // Tesseract uses + separator
      return `${primaryLanguage}+${additionalLanguage}`;
    }
    
    return primaryLanguage;
  },

  async runOCR() {
    this.isRunningOCR = true;

    const engineKey = window._ocrSelectedEngine || this.selectedEngine;
    const decodedEngine = this.decodeEngine(engineKey);
    const combinedLanguage = this.getCombinedLanguage();
    const { pathname } = window.location;
    const url = pathname.replace('/proofing/', '/api/ocr/') + `?engine=${decodedEngine}&language=${combinedLanguage}`;

    try {
      const response = await fetch(url);
      if (response.ok) {
        const contentType = response.headers.get('content-type') || '';
        if (contentType.includes('application/json')) {
          const payload = await response.json();
          this.applyOcrPayload(payload);
        } else {
          const content = await response.text();
          this.applyOcrPayload({ text: content });
        }
        this.showNotification('OCR completed successfully!', 'success');
      } else {
        const errorText = await this.getErrorMessage(response);
        this.showNotification(`OCR failed: ${errorText}`, 'error');
      }
    } catch (error) {
      console.error('OCR error:', error);
      this.showNotification('OCR failed: Network error', 'error');
    }

    this.isRunningOCR = false;
  },

  // Translation controls
  async runTranslation(engine = 'google', sourceLang = 'sa', targetLang = 'en', glossaries = []) {
    console.log('=== TRANSLATION DEBUG START ===');
    
    if (!this.pageDocument) {
      this.showNotification('No document content found to translate.', 'error');
      return;
    }

    this.isRunningTranslation = true;

    console.log('Starting translation:', { engine, sourceLang, targetLang, glossaries });
    console.log('Current pathname:', window.location.pathname);

    const { pathname } = window.location;
    let url = pathname.replace('/proofing/', '/api/translate/') + `?engine=${engine}&source_lang=${sourceLang}&target_lang=${targetLang}`;
    if (glossaries && glossaries.length > 0) {
      const glossaryVal = glossaries.includes('all') ? 'all' : glossaries.join(',');
      url += `&glossary=${encodeURIComponent(glossaryVal)}`;
    }
    
    console.log('Translation URL:', url);

    const headers = {
      'Content-Type': 'application/json',
    };
    const csrfInput = document.querySelector('input[name="csrf_token"]');
    if (csrfInput) {
      headers['X-CSRFToken'] = csrfInput.value;
    }

    try {
      console.log('Making POST fetch request...');
      const response = await fetch(url, {
        method: 'POST',
        headers: headers,
        body: JSON.stringify(this.pageDocument),
      });
      console.log('Translation response status:', response.status);
      
      if (response.ok) {
        const translatedDoc = await response.json();
        console.log('Translation result document:', translatedDoc);
        
        // Update pageDocument
        this.pageDocument = translatedDoc;
        this._flowPlainCache = documentToPlainText(this.pageDocument);
        
        // Update replica view
        if (this._replicaView) {
          this._replicaView.setDocument(this.pageDocument);
        }
        
        // Update flow / rich editor
        this._syncDocumentToForm();
        if (this.editorMode === 'flow') {
          this._applyFlowEditorContent();
        }
        this.hasUnsavedChanges = false;
        
        // Extract plain text for reference translation panel
        const translation = this._flowPlainCache;
        this.currentTranslation = translation;
        
        // Trigger translation display in the image box
        this.showTranslationInImageBox(translation, sourceLang, targetLang, engine);
        
        // Show success feedback
        this.showNotification('Translation completed successfully!', 'success');
      } else {
        const errorText = await this.getErrorMessage(response);
        console.error('Translation API error:', errorText);
        this.showNotification(`Translation failed: ${errorText}`, 'error');
      }
    } catch (error) {
      console.error('Translation error:', error);
      this.showNotification('Translation failed: Network error', 'error');
    }

    this.isRunningTranslation = false;
    console.log('=== TRANSLATION DEBUG END ===');
  },

  // Initialize translation language selector (now handled by Alpine.js)
  initTranslationSelector() {
    console.log('=== TRANSLATION SELECTOR INITIALIZED (Alpine.js) ===');
    // The translation selector is now handled directly by Alpine.js
    // No additional JavaScript needed
    this.fetchGlossaries();
  },

  async fetchGlossaries() {
    try {
      const { pathname } = window.location;
      const prefixMatch = pathname.match(/^(.*)\/proofing\//);
      const prefix = prefixMatch ? prefixMatch[1] : '';
      const response = await fetch(`${prefix}/api/glossaries`);
      if (response.ok) {
        this.allGlossaries = await response.json();
      } else {
        console.warn('Failed to fetch glossaries:', response.status);
      }
    } catch (error) {
      console.error('Error fetching glossaries:', error);
    }
  },

  get filteredGlossaries() {
    if (!this.allGlossaries) return [];
    const filtered = this.allGlossaries.filter(g => 
      g.source_language_code === this.sourceLanguage && 
      g.target_language_code === this.targetLanguage
    );
    this.selectedGlossaries = this.selectedGlossaries.filter(name => 
      name === 'all' || filtered.some(g => g.name === name)
    );
    return filtered;
  },

  get showGlossaryWarning() {
    return this.selectedGlossaries.includes('all') || this.selectedGlossaries.length > 3;
  },

  getGlossaryDisplayName(name) {
    const lookup = {
      'agri': 'Agriculture',
      'mech': 'Mechanical',
      'bio': 'Biology',
      'chem': 'Chemistry',
      'comp': 'Computer Science',
      'phy': 'Physics',
      'math': 'Mathematics',
      'it': 'Information Technology'
    };
    if (lookup[name]) {
      return lookup[name];
    }
    if (!name) return '';
    return name.charAt(0).toUpperCase() + name.slice(1);
  },

  // Show translation in the image box
  showTranslationInImageBox(translation, sourceLang, targetLang, engine) {
    console.log('=== DISPLAY DEBUG START ===');
    console.log('Attempting to display translation:', { translation, sourceLang, targetLang, engine });
    
    // Store translation data globally so Alpine.js can access it
    window.currentTranslationData = {
      content: translation,
      sourceLang: sourceLang,
      targetLang: targetLang,
      engine: engine
    };
    console.log('Stored translation data globally:', window.currentTranslationData);
    
    // Find the image box - be more specific to avoid dropdown elements
    let imageBox = document.querySelector('.bg-white.border.border-teal-200.rounded-lg.p-4.peacock-shadow[x-data*="showTranslation"]');
    if (!imageBox) {
      // Fallback: look for any element with showTranslation in x-data that's not a dropdown
      const allElements = document.querySelectorAll('[x-data*="showTranslation"]');
      for (const element of allElements) {
        if (!element.classList.contains('relative') && element.classList.contains('bg-white')) {
          imageBox = element;
          break;
        }
      }
    }
    if (!imageBox) {
      console.error('Image box not found');
      return;
    }
    console.log('Found image box:', imageBox);

    // Find the translation content area
    let translationArea = imageBox.querySelector('[x-show="showTranslation"]');
    if (!translationArea) {
      // Fallback: look for the div that contains the translation content
      translationArea = imageBox.querySelector('.w-full.h-\\[500px\\].bg-peacock-subtle.rounded-lg.border.border-teal-100.p-4.overflow-y-auto');
    }
    if (!translationArea) {
      console.error('Translation area not found');
      console.log('Available elements in imageBox:', imageBox.innerHTML);
      return;
    }
    console.log('Found translation area:', translationArea);

    // Remove the "no translation available" content first
    const noTranslationDiv = translationArea.querySelector('.flex.items-center.justify-center');
    if (noTranslationDiv) {
      console.log('Removing no translation div');
      noTranslationDiv.remove();
    }

    // Check if there's already a prose div, if not create one
    let proseDiv = translationArea.querySelector('.prose');
    if (!proseDiv) {
      console.log('Creating new prose div');
      proseDiv = document.createElement('div');
      proseDiv.className = 'prose max-w-none';
      translationArea.appendChild(proseDiv);
    }
    console.log('Prose div:', proseDiv);

    // Update the translation content
    const translationHTML = `
      <h4 class="text-lg font-semibold text-peacock-primary mb-3">
        Translation (${sourceLang.toUpperCase()} → ${targetLang.toUpperCase()})
        <span class="text-sm font-normal text-peacock-secondary">via ${engine}</span>
      </h4>
      <div class="text-sm leading-relaxed whitespace-pre-wrap">${translation}</div>
    `;
    
    console.log('Setting translation HTML:', translationHTML);
    proseDiv.innerHTML = translationHTML;

    // Show the translation view by setting Alpine.js data
    try {
      console.log('Attempting to set Alpine.js data...');
      // Try multiple ways to access Alpine.js data
      let alpineData = null;
      
      if (imageBox.__x && imageBox.__x.$data) {
        console.log('Using __x.$data');
        alpineData = imageBox.__x.$data;
      } else if (imageBox._x_dataStack && imageBox._x_dataStack[0]) {
        console.log('Using _x_dataStack[0]');
        alpineData = imageBox._x_dataStack[0];
      } else if (window.Alpine && imageBox._x_dataStack) {
        console.log('Using window.Alpine.$data');
        alpineData = window.Alpine.$data(imageBox);
      }
      
      console.log('Alpine data found:', alpineData);
      
      if (alpineData) {
        console.log('Setting Alpine.js data for translation');
        // Ensure dynamicTranslation is properly structured
        alpineData.dynamicTranslation = {
          content: window.currentTranslationData.content,
          sourceLang: window.currentTranslationData.sourceLang,
          targetLang: window.currentTranslationData.targetLang,
          engine: window.currentTranslationData.engine
        };
        alpineData.showTranslation = true;
        console.log('Alpine data after setting:', {
          dynamicTranslation: alpineData.dynamicTranslation,
          showTranslation: alpineData.showTranslation
        });
      } else {
        console.log('Alpine.js data not found, trying fallback');
        // Fallback: try to click the toggle button
        const toggleButton = imageBox.querySelector('button[title*="Toggle"]');
        if (toggleButton) {
          console.log('Clicking toggle button');
          toggleButton.click();
        } else {
          console.error('Toggle button not found');
        }
      }
    } catch (error) {
      console.error('Error setting Alpine.js data:', error);
      // Try clicking the toggle button as fallback
      const toggleButton = imageBox.querySelector('button[title*="Toggle"]');
      if (toggleButton) {
        toggleButton.click();
      }
    }

    console.log('=== DISPLAY DEBUG END ===');
  },

  async getErrorMessage(response) {
    const contentType = response.headers.get('content-type') || '';
    if (contentType.includes('application/json')) {
      try {
        const data = await response.json();
        return data.message || data.error || `Error ${response.status}`;
      } catch (e) {
        return `Error ${response.status}`;
      }
    }
    const text = await response.text();
    if (contentType.includes('text/html') || text.trim().startsWith('<!DOCTYPE') || text.trim().startsWith('<html')) {
      const match = text.match(/<h1[^>]*>([\s\S]*?)<\/h1>/i) || text.match(/<title[^>]*>([\s\S]*?)<\/title>/i);
      if (match && match[1]) {
        const cleanMsg = match[1].replace(/<[^>]*>/g, '').trim();
        if (cleanMsg.includes(response.status.toString())) {
          return cleanMsg;
        }
        return `${cleanMsg} (${response.status})`;
      }
      return `Server error (${response.status})`;
    }
    return text || `Error ${response.status}`;
  },

  // Simple notification system
  showNotification(message, type = 'info') {
    // Create notification element
    const notification = document.createElement('div');
    notification.className = `notification ${type}`;
    
    notification.innerHTML = `
      <div class="flex items-center gap-2">
        <span>${message}</span>
        <button onclick="this.parentElement.parentElement.remove()" class="ml-2 text-gray-500 hover:text-gray-700">
          <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12"></path>
          </svg>
        </button>
      </div>
    `;
    
    document.body.appendChild(notification);
    
    // Auto remove after 5 seconds
    setTimeout(() => {
      if (notification.parentElement) {
        notification.style.animation = 'slideOutRight 0.3s ease-in forwards';
        setTimeout(() => {
          if (notification.parentElement) {
            notification.remove();
          }
        }, 300);
      }
    }, 5000);
  },

  // Image zoom controls

  increaseImageZoom() {
    this.imageZoom *= 1.2;
    this.imageViewer.viewport.zoomTo(this.imageZoom);
    this.saveSettings();
  },
  decreaseImageZoom() {
    this.imageZoom *= 0.8;
    this.imageViewer.viewport.zoomTo(this.imageZoom);
    this.saveSettings();
  },
  resetImageZoom() {
    this.imageZoom = this.imageViewer.viewport.getHomeZoom();
    this.imageViewer.viewport.zoomTo(this.imageZoom);
    this.saveSettings();
  },

  // Image rotation controls

  rotateLeft() {
    if (this.imageViewer) {
      this.imageViewer.viewport.setRotation(this.imageViewer.viewport.getRotation() - 90);
    }
  },
  rotateRight() {
    if (this.imageViewer) {
      this.imageViewer.viewport.setRotation(this.imageViewer.viewport.getRotation() + 90);
    }
  },

  setupRotationButtons() {
    const rotateLeftBtn = document.getElementById('osd-rotate-left');
    const rotateRightBtn = document.getElementById('osd-rotate-right');
    
    if (rotateLeftBtn) {
      rotateLeftBtn.addEventListener('click', () => this.rotateLeft());
    }
    
    if (rotateRightBtn) {
      rotateRightBtn.addEventListener('click', () => this.rotateRight());
    }
  },

  // Text zoom controls

  increaseTextSize() {
    this.textZoom += 0.2;
    this.saveSettings();
  },
  decreaseTextSize() {
    this.textZoom = Math.max(0, this.textZoom - 0.2);
    this.saveSettings();
  },

  // Layout controls

  displaySideBySide() {
    this.layout = LAYOUT_SIDE_BY_SIDE;
    this.layoutClasses = this.getLayoutClasses();
    this.saveSettings();
  },
  displayTopAndBottom() {
    this.layout = LAYOUT_TOP_AND_BOTTOM;
    this.layoutClasses = this.getLayoutClasses();
    this.saveSettings();
  },

  // Markup controls

  changeSelectedText(callback) {
    // Get the textarea element
    const textarea = document.getElementById('content');
    if (!textarea) return;
    
    const start = textarea.selectionStart;
    const end = textarea.selectionEnd;
    const { value } = textarea;

    const selectedText = value.substr(start, end - start);
    const replacement = callback(selectedText);
    const newValue = value.substr(0, start) + replacement + value.substr(end);
    
    // Update both the DOM element and Alpine.js data
    textarea.value = newValue;
    this.content = newValue;

    // Update selection state and focus for better UX.
    textarea.setSelectionRange(start, start + replacement.length);
    textarea.focus();
  },
  markAsError() {
    this.changeSelectedText((s) => `<error>${s}</error>`);
  },
  markAsFix() {
    this.changeSelectedText((s) => `<fix>${s}</fix>`);
  },
  markAsUnclear() {
    this.changeSelectedText((s) => `<flag>${s}</flag>`);
  },
  markAsFootnoteNumber() {
    this.changeSelectedText((s) => `[^${s}]`);
  },
  replaceColonVisarga() {
    this.changeSelectedText((s) => s.replaceAll(':', 'ः'));
  },
  replaceSAvagraha() {
    this.changeSelectedText((s) => s.replaceAll('S', 'ऽ'));
  },
  transliterateSelection() {
    this.changeSelectedText((s) => Sanscript.t(s, this.fromScript, this.toScript));
    this.saveSettings();
  },

  // Character controls
  copyCharacter(e) {
    const character = e.target.textContent;
    navigator.clipboard.writeText(character);
  },
  
  _destroyConflictManualEditor() {
    if (this.conflictManualEditor) {
      try {
        destroyEditor(this.conflictManualEditor);
      } catch (e) {
        console.warn('Error destroying conflict manual editor:', e);
      }
      this.conflictManualEditor = null;
    }
  },

  useYourVersion() {
    this._destroyConflictManualEditor();
    this._applyContentToEditors(this.yourContent);
    this.showConflictResolver = false;
    this.hasUnsavedChanges = true;
    const versionField = document.querySelector('input[name="version"]');
    if (versionField && typeof window.PAGE_VERSION !== 'undefined') {
      versionField.value = window.PAGE_VERSION;
    }
  },

  useIncomingVersion() {
    this._destroyConflictManualEditor();
    this._applyContentToEditors(this.conflictContent);
    this.showConflictResolver = false;
    this.hasUnsavedChanges = false;
    const key = this._getStorageKey();
    if (key) localStorage.removeItem(key);
  },

  insertGitConflictMarkers() {
    this._destroyConflictManualEditor();
    const gitMerged = `<<<<<<< YOUR VERSION (Local Edits)\n${this.yourContent}\n=======\n${this.conflictContent}\n>>>>>>> INCOMING VERSION (Server Saved)`;
    this._applyContentToEditors(gitMerged);
    this.showConflictResolver = false;
    this.hasUnsavedChanges = true;
    const versionField = document.querySelector('input[name="version"]');
    if (versionField && typeof window.PAGE_VERSION !== 'undefined') {
      versionField.value = window.PAGE_VERSION;
    }
  },

  openManualResolver() {
    this.conflictViewMode = 'manual';
    setTimeout(() => {
      const localText = this.yourContent || (typeof window.YOUR_CONTENT !== 'undefined' ? window.YOUR_CONTENT : '') || (this.pageDocument ? documentToPlainText(this.pageDocument) : '') || this.content || '';
      const serverText = this.conflictContent || (typeof window.CONFLICT_CONTENT !== 'undefined' ? window.CONFLICT_CONTENT : '');
      
      const gitMergedRaw = `<<<<<<< YOUR VERSION (Local Edits)\n${localText}\n=======\n${serverText}\n>>>>>>> INCOMING VERSION (Server Saved)`;
      
      const gitMergedHtml = gitMergedRaw
        .split('\n')
        .map(line => {
          const escaped = line
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;');
          return `<p>${escaped || '<br>'}</p>`;
        })
        .join('');

      const editorEl = document.getElementById('conflict-tiptap-editor');
      if (editorEl) {
        if (!this.conflictManualEditor) {
          this.conflictManualEditor = createRichEditor('conflict-tiptap-editor', {
            content: gitMergedHtml,
          });
        } else {
          setEditorContent(this.conflictManualEditor, gitMergedHtml);
        }
      }
    }, 50);
  },

  applyManualResolution() {
    let resolvedText = '';
    if (this.conflictManualEditor) {
      resolvedText = getEditorText(this.conflictManualEditor) || getEditorContent(this.conflictManualEditor);
      this._destroyConflictManualEditor();
    } else {
      const localText = this.yourContent || this.content || '';
      const serverText = this.conflictContent || '';
      resolvedText = `<<<<<<< YOUR VERSION (Local Edits)\n${localText}\n=======\n${serverText}\n>>>>>>> INCOMING VERSION (Server Saved)`;
    }
    this._applyContentToEditors(resolvedText);
    this.showConflictResolver = false;
    this.hasUnsavedChanges = true;
    const versionField = document.querySelector('input[name="version"]');
    if (versionField && typeof window.PAGE_VERSION !== 'undefined') {
      versionField.value = window.PAGE_VERSION;
    }
  },

  dismissConflictResolver() {
    this._destroyConflictManualEditor();
    this.showConflictResolver = false;
  },

  checkVersionAndCacheOnLoad() {
    const key = this._getStorageKey();
    if (!key) return;
    const cachedStr = localStorage.getItem(key);
    if (!cachedStr) return;

    try {
      const parsed = JSON.parse(cachedStr);
      const docData = parsed.document || (parsed.blocks ? parsed : null);
      if (!docData) return;

      const cachedVer = parsed.version !== undefined ? parseInt(parsed.version, 10) : 0;
      const serverVer = (typeof window.PAGE_VERSION !== 'undefined') ? parseInt(window.PAGE_VERSION, 10) : 0;

      if (cachedVer !== undefined && cachedVer !== serverVer) {
        // Version mismatch between local backup (version X) and server (version Y)!
        const localText = parsed.content || (docData ? documentToPlainText(docData) : '');
        const serverText = (this.pageDocument ? documentToPlainText(this.pageDocument) : '') || this.content;

        if (localText && serverText && localText.trim() !== serverText.trim()) {
          this.yourContent = localText;
          this.conflictContent = serverText;
          this.conflictDiff = (typeof window.CONFLICT_DIFF !== 'undefined' && window.CONFLICT_DIFF)
            ? window.CONFLICT_DIFF
            : computeSimpleDiff(serverText, localText);
          this.showConflictResolver = true;
          this.conflictViewMode = 'split';
        }
      }
    } catch (e) {
      console.error('Error checking version and cache on load:', e);
    }
  },

  _applyContentToEditors(text) {
    this.content = text;
    const contentTextarea = document.getElementById('content');
    if (contentTextarea) {
      contentTextarea.value = text;
      contentTextarea.dispatchEvent(new Event('input', { bubbles: true }));
      contentTextarea.dispatchEvent(new Event('change', { bubbles: true }));
    }
    if (window.richEditorInstance) {
      try {
        this._isProgrammaticUpdate = true;
        setEditorContent(window.richEditorInstance, text);
        this._isProgrammaticUpdate = false;
      } catch (e) {
        console.warn('Failed to update rich editor content:', e);
      }
    }
    const newBlocks = blocksFromFlowHtml(text, this.pageDocument?.blocks || [], this.pageDocument?.content_format || 'blocks');
    if (newBlocks.length && this.pageDocument) {
      this.pageDocument = { ...this.pageDocument, blocks: newBlocks };
      const docField = document.getElementById('document');
      if (docField) docField.value = JSON.stringify(this.pageDocument);
      if (this._replicaView) {
        this._replicaView.setDocument(this.pageDocument);
      }
    }
  },

  _getStorageKey() {
    const pathMatch = window.location.pathname.match(/\/proofing\/([^\/]+)\/([^\/]+)/);
    if (pathMatch) {
      const targetKey = (typeof window.TARGET_VERSION_KEY !== 'undefined' && window.TARGET_VERSION_KEY) ? window.TARGET_VERSION_KEY : 'default';
      return `kalanjiyam-replica-doc-${pathMatch[1]}-${pathMatch[2]}-${targetKey}`;
    }
    return null;
  },

  // Sync editor content to textarea before form submission
  syncContentBeforeSubmit(event) {
    if (event) {
      event.preventDefault();
    }
    if (this.editorMode === 'flow') {
      const editor = window.richEditorInstance;
      if (editor) {
        const htmlContent = editor.getHTML();
        this.content = htmlContent;
        const textarea = document.getElementById('content');
        if (textarea) {
          textarea.value = htmlContent;
          textarea.dispatchEvent(new Event('input', { bubbles: true }));
          textarea.dispatchEvent(new Event('change', { bubbles: true }));
        }
      }
    } else if (this.pageDocument) {
      this._syncDocumentToForm();
      const textarea = document.getElementById('content');
      if (textarea) {
        textarea.dispatchEvent(new Event('input', { bubbles: true }));
        textarea.dispatchEvent(new Event('change', { bubbles: true }));
      }
      const docField = document.getElementById('document');
      if (docField) {
        docField.dispatchEvent(new Event('input', { bubbles: true }));
      }
    }
    this.hasUnsavedChanges = false;

    // Clear local storage cache right before programmatically submitting
    const key = this._getStorageKey();
    if (key) {
      localStorage.removeItem(key);
    }

    // Submit the form programmatically to ensure DOM fields are saved first
    const form = document.querySelector('form.book-editor-shell');
    if (form) {
      form.submit();
    }
  },
});

function computeSimpleDiff(oldText, newText) {
  if (!oldText && !newText) return '';
  if (oldText === newText) return escapeHtml(oldText || '');
  const oldLines = (oldText || '').split('\n');
  const newLines = (newText || '').split('\n');
  let result = [];
  let i = 0, j = 0;
  while (i < oldLines.length || j < newLines.length) {
    if (i < oldLines.length && j < newLines.length && oldLines[i] === newLines[j]) {
      result.push(escapeHtml(oldLines[i]));
      i++; j++;
    } else {
      if (i < oldLines.length) {
        result.push('<del class="bg-red-900/60 text-red-300 line-through px-1 rounded block mb-0.5">' + escapeHtml(oldLines[i]) + '</del>');
        i++;
      }
      if (j < newLines.length) {
        result.push('<ins class="bg-emerald-900/60 text-emerald-300 font-bold px-1 rounded block mb-0.5">' + escapeHtml(newLines[j]) + '</ins>');
        j++;
      }
    }
  }
  return result.join('\n');
}

function escapeHtml(str) {
  return (str || '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}
