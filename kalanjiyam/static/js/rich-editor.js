/* Rich text editor using TipTap */
/* global Editor, Image, Table, TableRow, TableCell, TableHeader, StarterKit, Underline, TextAlign */

import { Editor } from '@tiptap/core';
import { StarterKit } from '@tiptap/starter-kit';
import Image from '@tiptap/extension-image';
import Table from '@tiptap/extension-table';
import TableRow from '@tiptap/extension-table-row';
import TableCell from '@tiptap/extension-table-cell';
import TableHeader from '@tiptap/extension-table-header';
import Underline from '@tiptap/extension-underline';
import TextAlign from '@tiptap/extension-text-align';
import Link from '@tiptap/extension-link';
import Mathematics from '@tiptap/extension-mathematics';
import { marked } from 'marked';
import 'katex/dist/katex.min.css';


/**
 * Initialize TipTap editor instance
 * @param {string} elementId - ID of the DOM element to attach editor to
 * @param {Object} options - Configuration options
 * @param {string} options.content - Initial HTML content
 * @param {Function} options.onUpdate - Callback when content updates
 * @param {Function} options.onSelectionUpdate - Callback when selection changes
 * @returns {Editor} TipTap editor instance
 */
export function createRichEditor(elementId, options = {}) {
  const {
    content = '',
    onUpdate = () => {},
    onSelectionUpdate = () => {},
  } = options;

  const editor = new Editor({
    element: document.getElementById(elementId),
    extensions: [
      StarterKit.configure({
        // Disable default heading levels except h1-h3
        heading: {
          levels: [1, 2, 3],
        },
        // Keep code block and code
        codeBlock: true,
        code: true,
      }),
      Image.configure({
        inline: true,
        allowBase64: true, // Allow base64 for flexibility
        HTMLAttributes: {
          class: 'max-w-full h-auto rounded-lg',
        },
      }),
      Table.configure({
        resizable: true,
        HTMLAttributes: {
          class: 'border-collapse border border-teal-300 w-full',
        },
      }),
      TableRow.configure({
        HTMLAttributes: {
          class: 'border border-teal-300',
        },
      }),
      TableCell.configure({
        HTMLAttributes: {
          class: 'border border-teal-300 px-4 py-2',
        },
      }),
      TableHeader.configure({
        HTMLAttributes: {
          class: 'border border-teal-300 px-4 py-2 bg-peacock-light font-semibold',
        },
      }),
      Underline,
      TextAlign.configure({
        types: ['heading', 'paragraph'],
      }),
      Link.configure({
        openOnClick: false,
        HTMLAttributes: {
          class: 'text-peacock-primary underline hover:text-peacock-secondary',
        },
      }),
      Mathematics.configure({
        katexOptions: {
          throwOnError: false, // Don't throw errors for invalid LaTeX
        },
      }),
    ],
    content,
    onUpdate: ({ editor }) => {
      const html = editor.getHTML();
      onUpdate(html);
    },
    onSelectionUpdate: ({ editor }) => {
      onSelectionUpdate(editor);
    },
    editorProps: {
      attributes: {
        class: 'prose prose-lg max-w-none focus:outline-none min-h-[500px] px-4 py-6 text-base leading-relaxed',
      },
    },
  });

  return editor;
}

/**
 * Get HTML content from editor
 * @param {Editor} editor - TipTap editor instance
 * @returns {string} HTML content
 */
export function getEditorContent(editor) {
  if (!editor) return '';
  return editor.getHTML();
}

/**
 * Set HTML content in editor
 * @param {Editor} editor - TipTap editor instance
 * @param {string} html - HTML content to set
 */
export function setEditorContent(editor, html) {
  if (!editor) return;
  editor.commands.setContent(html || '');
}

/**
 * Get plain text content from editor
 * @param {Editor} editor - TipTap editor instance
 * @returns {string} Plain text content
 */
export function getEditorText(editor) {
  if (!editor) return '';
  return editor.getText();
}

/**
 * Set plain text content in editor
 * @param {Editor} editor - TipTap editor instance
 * @param {string} text - Plain text content to set
 */
export function setEditorText(editor, text) {
  if (!editor) return;

  if (!text) {
    editor.commands.setContent('');
    return;
  }

  try {
    marked.use({
      breaks: true,
      gfm: true,
    });

    let processedText = text;
    if (processedText) {
      processedText = processedText.replace(
        /([^\n])\s+(#{1,6}\s)/g,
        '$1\n\n$2',
      );
    }

    const htmlContent = marked.parse(processedText);
    editor.commands.setContent(htmlContent);
  } catch (error) {
    console.error('Error parsing Markdown content:', error);

    const escapedText = text
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#039;');

    const fallbackContent = escapedText
      .split('\n')
      .map((line) => `<p>${line || '<br>'}</p>`)
      .join('');

    editor.commands.setContent(fallbackContent);
  }
}

/**
 * Insert image into editor
 * @param {Editor} editor - TipTap editor instance
 * @param {string} imageUrl - URL of the image to insert
 */
export function insertImage(editor, imageUrl) {
  if (!editor || !editor.isEditable) {
    console.error('Editor not available or not editable');
    return false;
  }
  
  // Ensure URL is absolute (starts with / or http)
  let absoluteUrl = imageUrl;
  if (!imageUrl.startsWith('http') && !imageUrl.startsWith('/')) {
    absoluteUrl = '/' + imageUrl;
  }
  
  console.log('Attempting to insert image at cursor:', absoluteUrl);
  
  try {
    // Insert image at current selection
    // Using setImage command from TipTap Image extension
    editor.chain().focus().setImage({ src: absoluteUrl }).run();
    
    // Manual sync to textarea to ensure Alpine/form picks it up
    const textarea = document.getElementById('content');
    if (textarea) {
      textarea.value = editor.getHTML();
      textarea.dispatchEvent(new Event('input', { bubbles: true }));
      textarea.dispatchEvent(new Event('change', { bubbles: true }));
    }
    
    return true;
  } catch (error) {
    console.error('Error inserting image:', error);
    return false;
  }
}

/**
 * Force update editor by directly manipulating DOM
 * This is a last resort fallback
 */
function forceEditorUpdate(editor, htmlContent) {
  try {
    // Get the ProseMirror editor element
    const editorElement = editor.view.dom;
    if (editorElement) {
      // Try to find the editable content area
      const editableElement = editorElement.querySelector('.ProseMirror');
      if (editableElement) {
        // Parse HTML and update
        // This is a hack, but it should work
        console.warn('Using fallback DOM update method');
        
        // Create a temporary element to parse HTML
        const temp = document.createElement('div');
        temp.innerHTML = htmlContent;
        
        // The editor should sync on next interaction
        // For now, just ensure the textarea is correct
        const textarea = document.getElementById('content');
        if (textarea) {
          textarea.value = htmlContent;
        }
        
        // Trigger a focus event to force sync
        editorElement.dispatchEvent(new Event('focus', { bubbles: true }));
      }
    }
  } catch (error) {
    console.error('Force update failed:', error);
  }
}

/**
 * Sync editor content from textarea
 * This manually reads the textarea and updates the editor
 */
function syncEditorFromTextarea(editor) {
  try {
    const textarea = document.getElementById('content');
    if (!textarea) {
      console.error('Textarea not found for sync');
      return false;
    }
    
    const content = textarea.value;
    if (!content) {
      return false;
    }
    
    console.log('Syncing editor from textarea, content length:', content.length);
    
    // Try a different approach: destroy and recreate editor content
    // First, try simple setContent with retries
    let attempts = 0;
    const maxAttempts = 10;
    
    const trySync = () => {
      attempts++;
      try {
        // Clear and set in one go
        editor.commands.clearContent();
        
        setTimeout(() => {
          try {
            const success = editor.commands.setContent(content);
            
            // Verify after a delay
            setTimeout(() => {
              const actualContent = editor.getHTML();
              const imageFound = actualContent.includes('img') && actualContent.includes('src=');
              
              if (imageFound || actualContent.length > 10) {
                console.log('Editor synced from textarea successfully (attempt', attempts, ')');
                editor.commands.focus();
                // Scroll to bottom to show the image
                const editorElement = editor.view.dom;
                if (editorElement) {
                  editorElement.scrollTop = editorElement.scrollHeight;
                }
                return;
              } else if (attempts < maxAttempts) {
                console.log('Retrying sync, attempt', attempts + 1);
                setTimeout(trySync, 200);
              } else {
                console.warn('Editor sync failed after', maxAttempts, 'attempts');
                console.warn('Expected content length:', content.length, 'Actual:', actualContent.length);
                // Last resort: trigger a click event on the editor to force sync
                const editorElement = editor.view.dom;
                if (editorElement) {
                  editorElement.click();
                  editor.commands.focus();
                }
              }
            }, 150);
          } catch (setError) {
            if (attempts < maxAttempts) {
              setTimeout(trySync, 200);
            } else {
              console.error('setContent failed after', maxAttempts, 'attempts:', setError);
            }
          }
        }, 50);
      } catch (error) {
        if (attempts < maxAttempts) {
          setTimeout(trySync, 200);
        } else {
          console.error('Sync failed after', maxAttempts, 'attempts:', error);
        }
      }
    };
    
    trySync();
    return true;
  } catch (error) {
    console.error('Sync from textarea failed:', error);
    return false;
  }
}

/**
 * Insert table into editor
 * @param {Editor} editor - TipTap editor instance
 * @param {number} rows - Number of rows
 * @param {number} cols - Number of columns
 */
export function insertTable(editor, rows = 3, cols = 3) {
  if (!editor) return;
  editor.chain().focus().insertTable({ rows, cols, withHeaderRow: true }).run();
}

/**
 * Add row to table
 * @param {Editor} editor - TipTap editor instance
 */
export function addTableRow(editor) {
  if (!editor) return;
  editor.chain().focus().addRowBefore().run();
}

/**
 * Delete row from table
 * @param {Editor} editor - TipTap editor instance
 */
export function deleteTableRow(editor) {
  if (!editor) return;
  editor.chain().focus().deleteRow().run();
}

/**
 * Add column to table
 * @param {Editor} editor - TipTap editor instance
 */
export function addTableColumn(editor) {
  if (!editor) return;
  editor.chain().focus().addColumnBefore().run();
}

/**
 * Delete column from table
 * @param {Editor} editor - TipTap editor instance
 */
export function deleteTableColumn(editor) {
  if (!editor) return;
  editor.chain().focus().deleteColumn().run();
}

/**
 * Delete table
 * @param {Editor} editor - TipTap editor instance
 */
export function deleteTable(editor) {
  if (!editor) return;
  editor.chain().focus().deleteTable().run();
}

/**
 * Toggle bold formatting
 * @param {Editor} editor - TipTap editor instance
 */
export function toggleBold(editor) {
  if (!editor) return;
  editor.chain().focus().toggleBold().run();
}

/**
 * Toggle italic formatting
 * @param {Editor} editor - TipTap editor instance
 */
export function toggleItalic(editor) {
  if (!editor) return;
  editor.chain().focus().toggleItalic().run();
}

/**
 * Toggle underline formatting
 * @param {Editor} editor - TipTap editor instance
 */
export function toggleUnderline(editor) {
  if (!editor) return;
  editor.chain().focus().toggleUnderline().run();
}

/**
 * Check if editor command is active
 * @param {Editor} editor - TipTap editor instance
 * @param {string} command - Command name (e.g., 'bold', 'italic')
 * @returns {boolean} Whether command is active
 */
export function isCommandActive(editor, command) {
  if (!editor) return false;
  return editor.isActive(command);
}

/**
 * Destroy editor instance
 * @param {Editor} editor - TipTap editor instance
 */
export function destroyEditor(editor) {
  if (!editor) return;
  editor.destroy();
}

/**
 * Initialize toolbar with vanilla JavaScript (no Alpine.js)
 * This prevents Alpine.js Proxy wrappers from interfering with TipTap transactions
 * @param {Editor} editor - TipTap editor instance
 */
export function initializeToolbar(editor) {
  if (!editor) return;

  // Update toolbar button states
  const updateToolbarState = () => {
    // Bold
    const boldBtn = document.querySelector('[data-tiptap-command="toggleBold"]');
    if (boldBtn) {
      boldBtn.classList.toggle('active', editor.isActive('bold'));
    }

    // Italic
    const italicBtn = document.querySelector('[data-tiptap-command="toggleItalic"]');
    if (italicBtn) {
      italicBtn.classList.toggle('active', editor.isActive('italic'));
    }

    // Underline
    const underlineBtn = document.querySelector('[data-tiptap-command="toggleUnderline"]');
    if (underlineBtn) {
      underlineBtn.classList.toggle('active', editor.isActive('underline'));
    }

    // Bullet List
    const bulletListBtn = document.querySelector('[data-tiptap-command="toggleBulletList"]');
    if (bulletListBtn) {
      bulletListBtn.classList.toggle('active', editor.isActive('bulletList'));
    }

    // Ordered List
    const orderedListBtn = document.querySelector('[data-tiptap-command="toggleOrderedList"]');
    if (orderedListBtn) {
      orderedListBtn.classList.toggle('active', editor.isActive('orderedList'));
    }

    // Text align buttons
    ['left', 'center', 'right', 'justify'].forEach(align => {
      const btn = document.querySelector(`[data-tiptap-command="setTextAlign"][data-align="${align}"]`);
      if (btn) {
        btn.classList.toggle('active', editor.isActive({ textAlign: align }));
      }
    });

    // Link
    const linkBtn = document.querySelector('[data-tiptap-command="setLink"]');
    if (linkBtn) {
      linkBtn.classList.toggle('active', editor.isActive('link'));
    }

    // Undo/Redo state
    const undoBtn = document.querySelector('[data-tiptap-command="undo"]');
    if (undoBtn) {
      undoBtn.disabled = !editor.can().undo();
    }

    const redoBtn = document.querySelector('[data-tiptap-command="redo"]');
    if (redoBtn) {
      redoBtn.disabled = !editor.can().redo();
    }
  };

  // Execute toolbar command
  const executeCommand = (command, options = {}) => {
    try {
      switch (command) {
        case 'undo':
          editor.chain().focus().undo().run();
          break;
        case 'redo':
          editor.chain().focus().redo().run();
          break;
        case 'toggleBold':
          editor.chain().focus().toggleBold().run();
          break;
        case 'toggleItalic':
          editor.chain().focus().toggleItalic().run();
          break;
        case 'toggleUnderline':
          editor.chain().focus().toggleUnderline().run();
          break;
        case 'setParagraph':
          editor.chain().focus().setParagraph().run();
          break;
        case 'toggleHeading':
          if (options.level) {
            editor.chain().focus().toggleHeading({ level: options.level }).run();
          }
          break;
        case 'toggleBulletList':
          editor.chain().focus().toggleBulletList().run();
          break;
        case 'toggleOrderedList':
          editor.chain().focus().toggleOrderedList().run();
          break;
        case 'setTextAlign':
          const align = options.align || 'left';
          editor.chain().focus().setTextAlign(align).run();
          break;
        case 'setLink':
          if (options.href) {
            const { selection } = editor.state;
            if (selection.empty) {
              editor.chain().focus().insertContent(`<a href="${options.href}">${options.href}</a>`).run();
            } else {
              editor.chain().focus().setLink({ href: options.href }).run();
            }
          }
          break;
        case 'unsetLink':
          editor.chain().focus().unsetLink().run();
          break;
        case 'insertTable':
          if (options.rows && options.cols) {
            editor.chain().focus().insertTable({
              rows: options.rows,
              cols: options.cols,
              withHeaderRow: options.withHeaderRow || false,
            }).run();
          }
          break;
        default:
          console.warn('Unknown toolbar command:', command);
      }
      
      // Update toolbar state after command
      updateToolbarState();
    } catch (error) {
      console.error('Error executing toolbar command:', command, error);
    }
  };

  // Attach event listeners to all toolbar buttons
  document.querySelectorAll('[data-tiptap-command]').forEach(button => {
    button.addEventListener('click', (e) => {
      e.preventDefault();
      const command = button.dataset.tiptapCommand;
      const options = {};
      
      // Get additional options from data attributes
      if (button.dataset.level) {
        options.level = parseInt(button.dataset.level);
      }
      if (button.dataset.align) {
        options.align = button.dataset.align;
      }
      
      executeCommand(command, options);
    });
  });

  // Update toolbar state on editor updates
  editor.on('selectionUpdate', updateToolbarState);
  editor.on('update', updateToolbarState);

  // Initial toolbar state update
  updateToolbarState();

  // Return cleanup function
  return () => {
    editor.off('selectionUpdate', updateToolbarState);
    editor.off('update', updateToolbarState);
  };
}

