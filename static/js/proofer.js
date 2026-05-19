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

  // OCR settings
  selectedEngine: '1', // Default to Google OCR (1)
  selectedLanguage: 'sa',

  // Translation settings
  selectedTranslationEngine: 'google',
  sourceLanguage: 'hi',
  targetLanguage: 'en',

  // Internal-only
  layoutClasses: CLASSES_SIDE_BY_SIDE,
  isRunningOCR: false,
  isRunningTranslation: false,
  hasUnsavedChanges: false,
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
    this.loadSettings();
    this.layoutClasses = this.getLayoutClasses();

    // Initialize content from the textarea if it exists
    const textarea = document.getElementById('content');
    if (textarea && textarea.value) {
      this.content = textarea.value;
    }

    // Set `imageZoom` only after the viewer is fully initialized.
    this.imageViewer = initializeImageViewer(IMAGE_URL);
    this.imageViewer.addHandler('open', () => {
      this.imageZoom = this.imageZoom || this.imageViewer.viewport.getHomeZoom();
      this.imageViewer.viewport.zoomTo(this.imageZoom);
    });

    // Use `.bind(this)` so that `this` in the function refers to this app and
    // not `window`.
    window.onbeforeunload = this.onBeforeUnload.bind(this);
    
    // Initialize language options
    this.updateLanguageOptions();
    
    // Add event listeners for rotation buttons
    this.setupRotationButtons();
    
    // Initialize translation selector
    this.initTranslationSelector();
    
    // Initialize rich text editor
    this.initRichEditor();
  },
  
  // Initialize TipTap rich text editor
  initRichEditor() {
    // Wait for DOM to be ready using setTimeout
    setTimeout(() => {
      const editorElement = document.getElementById('rich-editor');
      // Check window.richEditorInstance instead of this.richEditor to avoid Alpine Proxy
      if (editorElement && !window.richEditorInstance) {
        // Get initial content from textarea if it exists, or from Alpine.js content
        const textarea = document.getElementById('content');
        const initialContent = textarea ? textarea.value : this.content;
        
        // Store editor instance on window to avoid Alpine.js Proxy wrapping
        // This is CRITICAL - Alpine's Proxy causes transaction state conflicts
        const editor = createRichEditor('rich-editor', {
          content: initialContent || '',
          onUpdate: (html) => {
            // Simple: just sync the content
            this.content = html;
            const contentTextarea = document.getElementById('content');
            if (contentTextarea) {
              contentTextarea.value = html;
            }
            this.hasUnsavedChanges = true;
          },
          onSelectionUpdate: () => {
            // No-op - toolbar handles its own state now
          },
        });
        
        // Store globally to avoid Alpine Proxy
        window.richEditorInstance = editor;
        
        // Also store as non-reactive property (Alpine ignores properties starting with _)
        // This allows Alpine methods to access it if needed
        this._richEditor = editor;
        
        // Set simple boolean flag for Alpine (for showing/hiding toolbar)
        this.richEditor = true;
        
        // Initialize toolbar with vanilla JS (NO Alpine.js)
        if (editor) {
          initializeToolbar(editor);
          console.log('Editor and toolbar initialized with vanilla JS (stored outside Alpine)');
        }
        
        // Store reference for manual sync
        this.syncEditorFromTextarea = () => {
          const textarea = document.getElementById('content');
          if (textarea && window.richEditorInstance) {
            const content = textarea.value;
            if (content) {
              try {
                window.richEditorInstance.commands.setContent(content);
              } catch (e) {
                console.error('Manual sync failed:', e);
              }
            }
          }
        };
        
        // Make uploadImageFiles available globally for drag-and-drop
        window.uploadImageFiles = this.uploadImageFiles.bind(this);
      }
    }, 100);
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
        const errorText = await response.text();
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

  updateLanguageOptions() {
    // Small delay to ensure DOM is updated
    setTimeout(() => {
      const engine = this.selectedEngine;
      const engineConfig = this.ocrEngines[engine];
      const languageSelect = document.getElementById('language-select');
      const additionalLanguageSelect = document.getElementById('additional-language-select');
      
      if (!languageSelect || !engineConfig) {
        return;
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
      // Try to map the language to the new engine's equivalent
      const languageMap = {
        'sa': 'san',  // Google Sanskrit -> Tesseract Sanskrit
        'san': 'sa',  // Tesseract Sanskrit -> Google Sanskrit
        'en': 'eng',  // Google English -> Tesseract English
        'eng': 'en',  // Tesseract English -> Google English
        'hi': 'hin',  // Google Hindi -> Tesseract Hindi
        'hin': 'hi',  // Tesseract Hindi -> Google Hindi
        // Add more mappings as needed
      };
      
      const mappedLanguage = languageMap[this.selectedLanguage];
      if (mappedLanguage && engineConfig.languages.find(lang => lang.value === mappedLanguage)) {
        this.selectedLanguage = mappedLanguage;
      } else {
        this.selectedLanguage = engineConfig.languages[0].value;
      }
    }
    
    // Update additional language options for bilingual support
    if (additionalLanguageSelect && (engine === '2' || engine === '3')) {
      additionalLanguageSelect.innerHTML = '<option value="">{{ _("None") }}</option>';
      
      engineConfig.languages.forEach(lang => {
        const option = document.createElement('option');
        option.value = lang.value;
        option.textContent = lang.text;
        additionalLanguageSelect.appendChild(option);
      });
    }
    }, 10); // Small delay to ensure DOM is ready
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
      '7': 'qwen3'
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

  async runOCR(engine = '1', language = 'sa') {
    this.isRunningOCR = true;

    const decodedEngine = this.decodeEngine(engine);
    const combinedLanguage = this.getCombinedLanguage();
    const { pathname } = window.location;
    const url = pathname.replace('/proofing/', '/api/ocr/') + `?engine=${decodedEngine}&language=${combinedLanguage}`;

    try {
      const response = await fetch(url);
      if (response.ok) {
        const content = await response.text();
        
        // Update the rich editor if available
        const editor = window.richEditorInstance;
        if (editor) {
          // OCR returns plain text, so use setEditorText
          setEditorText(editor, content);
          // Get HTML content from editor
          this.content = getEditorContent(editor);
        } else {
          // Fallback to textarea if editor not initialized
          this.content = content;
          const textarea = document.getElementById('content');
          if (textarea) {
            textarea.value = content;
          }
        }
        
        // Show success feedback
        this.showNotification('OCR completed successfully!', 'success');
      } else {
        const errorText = await response.text();
        this.showNotification(`OCR failed: ${errorText}`, 'error');
      }
    } catch (error) {
      console.error('OCR error:', error);
      this.showNotification('OCR failed: Network error', 'error');
    }

    this.isRunningOCR = false;
  },

  // Translation controls
  async runTranslation(engine = 'google', sourceLang = 'sa', targetLang = 'en') {
    console.log('=== TRANSLATION DEBUG START ===');
    
    // Get content from rich editor if available, otherwise use content property
    let contentToTranslate = this.content;
    const editor = window.richEditorInstance;
    if (editor) {
      // Get plain text from editor for translation
      contentToTranslate = editor.getText();
    }
    
    // Check if there's content to translate
    if (!contentToTranslate || contentToTranslate.trim() === '') {
      console.log('No content to translate');
      this.showNotification('No content to translate. Please add some text to the editor first.', 'error');
      return;
    }

    this.isRunningTranslation = true;

    console.log('Starting translation:', { engine, sourceLang, targetLang });
    console.log('Content to translate:', contentToTranslate);
    console.log('Current pathname:', window.location.pathname);

    const { pathname } = window.location;
    // Send content as POST body for translation
    const url = pathname.replace('/proofing/', '/api/translate/') + `?engine=${engine}&source_lang=${sourceLang}&target_lang=${targetLang}`;
    
    console.log('Translation URL:', url);

    try {
      console.log('Making fetch request...');
      const response = await fetch(url);
      console.log('Translation response status:', response.status);
      console.log('Response headers:', Object.fromEntries(response.headers.entries()));
      
      if (response.ok) {
        const translation = await response.text();
        console.log('Translation result:', translation);
        console.log('Translation result length:', translation.length);
        
        // Check if translation is empty or just whitespace
        if (!translation || translation.trim() === '') {
          console.warn('Translation result is empty');
          this.showNotification('Translation result is empty. Please ensure there is content to translate.', 'error');
          return;
        }
        
        // Show success feedback
        this.showNotification('Translation completed successfully!', 'success');
        
        // Store translation in a variable that can be accessed by the image box
        this.currentTranslation = translation;
        
        // Trigger translation display in the image box
        this.showTranslationInImageBox(translation, sourceLang, targetLang, engine);
      } else {
        const errorText = await response.text();
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
  
  // Sync editor content to textarea before form submission
  syncContentBeforeSubmit() {
    // Ensure textarea has the latest content from editor
    const editor = window.richEditorInstance;
    if (editor) {
      const htmlContent = editor.getHTML();
      
      // Update Alpine.js content first (this drives the textarea via x-model)
      this.content = htmlContent;
      
      // Also update textarea directly as backup
      const textarea = document.getElementById('content');
      if (textarea) {
        textarea.value = htmlContent;
        // Trigger events to ensure form recognizes the change
        textarea.dispatchEvent(new Event('input', { bubbles: true }));
        textarea.dispatchEvent(new Event('change', { bubbles: true }));
      }
      
      console.log('Synced editor content before submit. Length:', htmlContent.length);
      console.log('Content preview:', htmlContent.substring(0, 200));
    }
    this.hasUnsavedChanges = false;
  },
});
