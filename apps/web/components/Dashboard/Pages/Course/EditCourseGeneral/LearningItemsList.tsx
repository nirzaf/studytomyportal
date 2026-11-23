import React, { useState, useEffect, useRef, memo, useCallback, useMemo, lazy, Suspense } from 'react';
import { useTranslation } from 'react-i18next';
import { Plus, X, Link as LinkIcon, ExternalLink, GripVertical, ArrowDown, ArrowUp } from 'lucide-react';
const Picker = lazy(() => import('@emoji-mart/react'));

interface LearningItem {
  id: string;
  text: string;
  emoji: string;
  link?: string;
}

interface LearningItemsListProps {
  value: string;
  onChange: (value: string) => void;
  error?: string;
}

const parseItems = (value: string): LearningItem[] => {
  if (!value) return [];
  try {
    const parsed = JSON.parse(value);
    if (Array.isArray(parsed)) {
      return parsed.filter((it): it is LearningItem => it && typeof it === 'object' && typeof it.id === 'string');
    }
  } catch {
    // fall through
  }
  return [];
};

const newId = () => `l_${Date.now().toString(36)}_${Math.random().toString(36).slice(2, 7)}`;

const LearningItemsList = ({ value, onChange, error }: LearningItemsListProps) => {
  const { t } = useTranslation();
  // Fully controlled: items are always derived from `value`.
  // Tab switches and remounts stay in sync without a local shadow copy.
  const items = useMemo(() => parseItems(value), [value]);

  const [showEmojiPicker, setShowEmojiPicker] = useState<string | null>(null);
  const [expandedLink, setExpandedLink] = useState<string | null>(null);
  const [dragId, setDragId] = useState<string | null>(null);

  const pickerRef = useRef<HTMLDivElement>(null);
  const inputRefs = useRef<Record<string, HTMLInputElement | null>>({});
  const linkInputRefs = useRef<Record<string, HTMLInputElement | null>>({});

  const commit = useCallback((next: LearningItem[]) => {
    onChange(JSON.stringify(next));
  }, [onChange]);

  const addItem = useCallback(() => {
    const id = newId();
    commit([...items, { id, text: '', emoji: '📝' }]);
    setTimeout(() => inputRefs.current[id]?.focus(), 0);
  }, [items, commit]);

  const removeItem = useCallback((id: string) => {
    commit(items.filter(it => it.id !== id));
  }, [items, commit]);

  const updateText = useCallback((id: string, text: string) => {
    commit(items.map(it => it.id === id ? { ...it, text } : it));
  }, [items, commit]);

  const updateEmoji = useCallback((id: string, emoji: string) => {
    commit(items.map(it => it.id === id ? { ...it, emoji } : it));
    setShowEmojiPicker(null);
    setTimeout(() => inputRefs.current[id]?.focus(), 0);
  }, [items, commit]);

  const updateLink = useCallback((id: string, link: string) => {
    commit(items.map(it => it.id === id ? { ...it, link } : it));
  }, [items, commit]);

  const clearLink = useCallback((id: string) => {
    commit(items.map(it => it.id === id ? { ...it, link: '' } : it));
    setExpandedLink(null);
  }, [items, commit]);

  const moveItem = useCallback((id: string, direction: -1 | 1) => {
    const index = items.findIndex(item => item.id === id);
    const destination = index + direction;
    if (index < 0 || destination < 0 || destination >= items.length) return;

    const next = [...items];
    [next[index], next[destination]] = [next[destination], next[index]];
    commit(next);
    setTimeout(() => inputRefs.current[id]?.focus(), 0);
  }, [items, commit]);

  // Close emoji picker when clicking outside
  useEffect(() => {
    if (!showEmojiPicker) return;
    const handler = (e: MouseEvent) => {
      if (pickerRef.current && !pickerRef.current.contains(e.target as Node)) {
        setShowEmojiPicker(null);
      }
    };
    document.addEventListener('mousedown', handler);
    return () => document.removeEventListener('mousedown', handler);
  }, [showEmojiPicker]);

  // Drag and drop reordering
  const onDragStart = (id: string) => (e: React.DragEvent) => {
    setDragId(id);
    e.dataTransfer.effectAllowed = 'move';
  };
  const onDragOver = (e: React.DragEvent) => {
    e.preventDefault();
    e.dataTransfer.dropEffect = 'move';
  };
  const onDrop = (targetId: string) => (e: React.DragEvent) => {
    e.preventDefault();
    if (!dragId || dragId === targetId) { setDragId(null); return; }
    const from = items.findIndex(it => it.id === dragId);
    const to = items.findIndex(it => it.id === targetId);
    if (from === -1 || to === -1) { setDragId(null); return; }
    const next = [...items];
    const [moved] = next.splice(from, 1);
    next.splice(to, 0, moved);
    commit(next);
    setDragId(null);
  };

  const isEmpty = items.length === 0;

  return (
    <div className="space-y-1.5">
      <div className="rounded-xl border border-gray-200 bg-white divide-y divide-gray-100">
        {isEmpty && (
          <div className="px-4 py-8 text-center">
            <div className="text-sm text-gray-400 mb-1">{t('dashboard.courses.general.learning_items.empty_title')}</div>
            <div className="text-xs text-gray-300">{t('dashboard.courses.general.learning_items.empty_hint')}</div>
          </div>
        )}

        {items.map((item, index) => {
          const isDragging = dragId === item.id;
          return (
            <div
              key={item.id}
              className={`group relative ${isDragging ? 'opacity-40' : ''}`}
              onDragOver={onDragOver}
              onDrop={onDrop(item.id)}
            >
              <div className="flex flex-wrap items-center gap-2 px-2.5 py-2 sm:flex-nowrap">
                {/* Drag handle */}
                <div
                  draggable
                  onDragStart={onDragStart(item.id)}
                  onDragEnd={() => setDragId(null)}
                  className="hidden shrink-0 cursor-grab p-1 text-gray-400 hover:text-gray-600 active:cursor-grabbing sm:flex"
                  title={t('library.sort.drag_to_reorder')}
                  aria-hidden="true"
                >
                  <GripVertical size={16} />
                </div>

                {/* Emoji button */}
                <div className="relative shrink-0">
                  <button
                    type="button"
                    onClick={() => setShowEmojiPicker(showEmojiPicker === item.id ? null : item.id)}
                    className="flex size-10 shrink-0 items-center justify-center rounded-md text-xl leading-none transition-colors hover:bg-gray-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary focus-visible:ring-offset-2"
                    title={t('dashboard.courses.general.learning_items.change_emoji')}
                    aria-label={t('dashboard.courses.general.learning_items.change_emoji_item', { index: index + 1 })}
                  >
                    {item.emoji}
                  </button>
                  {showEmojiPicker === item.id && (
                    <div ref={pickerRef} className="absolute z-50 top-9 start-0">
                      <Suspense fallback={
                        <div className="p-3 text-xs text-gray-400 bg-white border border-gray-200 rounded-lg shadow-lg w-[280px]">
                          {t('common.loading')}
                        </div>
                      }>
                        <Picker
                          onEmojiSelect={(e: any) => updateEmoji(item.id, e.native)}
                          theme="light"
                          previewPosition="none"
                          searchPosition="top"
                          maxFrequentRows={0}
                          autoFocus={false}
                        />
                      </Suspense>
                    </div>
                  )}
                </div>

                {/* Text input */}
                <input
                  ref={(el) => { inputRefs.current[item.id] = el; }}
                  type="text"
                  name={`learning-outcome-${item.id}`}
                  aria-label={t('dashboard.courses.general.learning_items.input_label', { index: index + 1 })}
                  value={item.text}
                  onChange={(e) => updateText(item.id, e.target.value)}
                  placeholder={t('dashboard.courses.general.learning_items.placeholder')}
                  className="order-2 min-w-[calc(100%-3rem)] flex-1 rounded-md border border-transparent bg-transparent px-2 py-2 text-sm text-gray-900 outline-none placeholder:text-gray-400 focus-visible:border-gray-200 focus-visible:ring-2 focus-visible:ring-primary focus-visible:ring-offset-2 sm:order-none sm:min-w-0"
                />

                {/* Link chip (when link is set) */}
                {item.link && expandedLink !== item.id && (
                  <button
                    type="button"
                    onClick={() => setExpandedLink(item.id)}
                    className="order-3 flex shrink-0 items-center gap-1 rounded-full bg-blue-50 px-2 py-1 text-[11px] text-blue-700 transition-colors hover:bg-blue-100 sm:order-none"
                    title={item.link}
                    aria-label={t('dashboard.courses.general.learning_items.edit_link_for_item', { index: index + 1 })}
                  >
                    <ExternalLink size={10} />
                    <span className="max-w-[96px] truncate">{item.link.replace(/^https?:\/\//, '')}</span>
                  </button>
                )}

                {/* Action buttons, always visible but muted */}
                <div className="order-4 ms-auto flex shrink-0 items-center gap-0.5 sm:order-none sm:ms-0">
                  <button
                    type="button"
                    onClick={() => moveItem(item.id, -1)}
                    disabled={index === 0}
                    className="flex size-10 items-center justify-center rounded-md text-gray-500 transition-colors hover:bg-gray-100 hover:text-gray-900 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary focus-visible:ring-offset-2 disabled:cursor-not-allowed disabled:opacity-35"
                    aria-label={t('dashboard.courses.general.learning_items.move_up', { index: index + 1 })}
                    title={t('dashboard.courses.general.learning_items.move_up', { index: index + 1 })}
                  >
                    <ArrowUp size={15} aria-hidden="true" />
                  </button>
                  <button
                    type="button"
                    onClick={() => moveItem(item.id, 1)}
                    disabled={index === items.length - 1}
                    className="flex size-10 items-center justify-center rounded-md text-gray-500 transition-colors hover:bg-gray-100 hover:text-gray-900 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary focus-visible:ring-offset-2 disabled:cursor-not-allowed disabled:opacity-35"
                    aria-label={t('dashboard.courses.general.learning_items.move_down', { index: index + 1 })}
                    title={t('dashboard.courses.general.learning_items.move_down', { index: index + 1 })}
                  >
                    <ArrowDown size={15} aria-hidden="true" />
                  </button>
                  {!item.link && (
                    <button
                      type="button"
                      onClick={() => {
                        setExpandedLink(expandedLink === item.id ? null : item.id);
                        setTimeout(() => linkInputRefs.current[item.id]?.focus(), 0);
                      }}
                      className="flex size-10 items-center justify-center rounded-md text-gray-500 transition-colors hover:bg-blue-50 hover:text-blue-600 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary focus-visible:ring-offset-2"
                      title={t('dashboard.courses.general.learning_items.add_link')}
                      aria-label={t('dashboard.courses.general.learning_items.add_link_for_item', { index: index + 1 })}
                    >
                      <LinkIcon size={13} />
                    </button>
                  )}
                  <button
                    type="button"
                    onClick={() => removeItem(item.id)}
                    className="flex size-10 items-center justify-center rounded-md text-gray-500 transition-colors hover:bg-red-50 hover:text-red-600 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary focus-visible:ring-offset-2"
                    title={t('boards.members.remove')}
                    aria-label={t('dashboard.courses.general.learning_items.remove_item', { index: index + 1 })}
                  >
                    <X size={13} />
                  </button>
                </div>
              </div>

              {/* Inline link editor */}
              {expandedLink === item.id && (
                <div className="px-12 pb-2.5 -mt-0.5">
                  <div className="flex items-center gap-2 bg-gray-50 rounded-md px-2.5 py-1.5 border border-gray-100">
                    <LinkIcon size={12} className="text-gray-400 shrink-0" />
                    <input
                      ref={(el) => { linkInputRefs.current[item.id] = el; }}
                      type="url"
                      aria-label={t('dashboard.courses.general.learning_items.link_label', { index: index + 1 })}
                      value={item.link || ''}
                      onChange={(e) => updateLink(item.id, e.target.value)}
                      onBlur={() => { if (!item.link) setExpandedLink(null); }}
                      placeholder="https://..."
                      className="min-w-0 flex-1 rounded-md bg-transparent px-2 py-1 text-sm text-gray-900 outline-none placeholder:text-gray-400 focus-visible:ring-2 focus-visible:ring-primary focus-visible:ring-offset-2"
                      autoFocus
                    />
                    {item.link ? (
                      <button
                        type="button"
                        onClick={() => clearLink(item.id)}
                        className="min-h-10 shrink-0 rounded px-2 text-xs text-gray-600 transition-colors hover:text-red-600 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary"
                      >
                        {t('boards.members.remove')}
                      </button>
                    ) : (
                      <button
                        type="button"
                        onClick={() => setExpandedLink(null)}
                        className="min-h-10 shrink-0 rounded px-2 text-xs text-gray-600 transition-colors hover:text-gray-900 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary"
                      >
                        {t('common.close')}
                      </button>
                    )}
                  </div>
                </div>
              )}
            </div>
          );
        })}

        <button
          type="button"
          onClick={addItem}
          className="flex min-h-11 w-full items-center gap-2 rounded-b-xl px-4 py-2.5 text-sm text-gray-700 transition-colors hover:bg-gray-50 hover:text-gray-900 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-primary"
        >
          <Plus size={13} className="text-blue-500 shrink-0" />
          {t('dashboard.courses.general.learning_items.add')}
        </button>
      </div>

      {error && (
        <p className="text-xs text-red-500 px-1">{error}</p>
      )}
    </div>
  );
};

export default memo(LearningItemsList);
