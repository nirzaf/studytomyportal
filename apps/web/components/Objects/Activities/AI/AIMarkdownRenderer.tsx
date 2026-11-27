'use client'
import React from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import rehypeHighlight from 'rehype-highlight'
import { MarkdownImageLink } from './MarkdownImageLink'

type AIMarkdownRendererProps = {
  content: string
  isStreaming?: boolean
  theme?: 'light' | 'dark'
}

function AIMarkdownRenderer({ content, isStreaming = false, theme = 'dark' }: AIMarkdownRendererProps) {
  const light = theme === 'light'
  return (
    <div className={`ai-markdown-content prose prose-sm max-w-none ${light ? 'text-slate-800' : 'prose-invert'}`}>
      <style jsx global>{`
        @keyframes cursor-blink {
          0%, 100% { opacity: 1; }
          50% { opacity: 0; }
        }
        .streaming-cursor {
          animation: cursor-blink 0.8s ease-in-out infinite;
        }
      `}</style>
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        rehypePlugins={[rehypeHighlight]}
        components={{
          // Headings
          h1: ({ children }) => (
            <h1 className={`text-lg font-bold ${light ? 'text-slate-900' : 'text-white/90'} mt-4 mb-2 first:mt-0`}>{children}</h1>
          ),
          h2: ({ children }) => (
            <h2 className={`text-base font-bold ${light ? 'text-slate-900' : 'text-white/90'} mt-3 mb-2 first:mt-0`}>{children}</h2>
          ),
          h3: ({ children }) => (
            <h3 className={`text-sm font-bold ${light ? 'text-slate-900' : 'text-white/90'} mt-2 mb-1 first:mt-0`}>{children}</h3>
          ),
          // Paragraph
          p: ({ children }) => (
            <p className={`${light ? 'text-slate-800' : 'text-white/80'} text-sm leading-relaxed mb-2 last:mb-0`}>{children}</p>
          ),
          // Bold and italic
          strong: ({ children }) => (
            <strong className={`font-semibold ${light ? 'text-slate-900' : 'text-white/90'}`}>{children}</strong>
          ),
          em: ({ children }) => (
            <em className={`italic ${light ? 'text-slate-800' : 'text-white/80'}`}>{children}</em>
          ),
          // Lists
          ul: ({ children }) => (
            <ul className={`list-disc list-inside ${light ? 'text-slate-800' : 'text-white/80'} text-sm mb-2 space-y-1 ms-2`}>
              {children}
            </ul>
          ),
          ol: ({ children }) => (
            <ol className={`list-decimal list-inside ${light ? 'text-slate-800' : 'text-white/80'} text-sm mb-2 space-y-1 ms-2`}>
              {children}
            </ol>
          ),
          li: ({ children }) => (
            <li className={`${light ? 'text-slate-800' : 'text-white/80'}`}>{children}</li>
          ),
          // Code blocks
          code: ({ className, children, ...props }) => {
            const isInline = !className
            if (isInline) {
              return (
                <code
                  className={`${light ? 'bg-slate-100' : 'bg-white/10'} ${light ? 'text-violet-800' : 'text-purple-300'} px-1.5 py-0.5 rounded text-xs font-mono`}
                  {...props}
                >
                  {children}
                </code>
              )
            }
            return (
              <code
                className={`${className} block ${light ? 'bg-slate-100' : 'bg-black/40'} rounded-lg p-3 text-xs font-mono overflow-x-auto my-2`}
                {...props}
              >
                {children}
              </code>
            )
          },
          pre: ({ children }) => (
            <pre className={`${light ? 'bg-slate-100' : 'bg-black/40'} rounded-lg overflow-x-auto my-2`}>
              {children}
            </pre>
          ),
          // Blockquote
          blockquote: ({ children }) => (
            <blockquote className={`border-s-2 border-purple-500/50 ps-3 my-2 ${light ? 'text-slate-700' : 'text-white/70'} italic`}>
              {children}
            </blockquote>
          ),
          // Links
          a: ({ href, children }) => (
            <a
              href={href}
              target="_blank"
              rel="noopener noreferrer"
              className={`${light ? 'text-violet-800 hover:text-violet-950' : 'text-purple-400 hover:text-purple-300'} underline`}
            >
              {children}
            </a>
          ),
          // Images render as links, never loaded
          img: ({ src, alt }) => (
            <MarkdownImageLink src={src} alt={alt} className={`${light ? 'text-violet-800 hover:text-violet-950' : 'text-purple-400 hover:text-purple-300'} underline`} />
          ),
          // Horizontal rule
          hr: () => <hr className={`${light ? 'border-slate-200' : 'border-white/10'} my-3`} />,
          // Table
          table: ({ children }) => (
            <div className="overflow-x-auto my-2">
              <table className="min-w-full text-xs border-collapse">
                {children}
              </table>
            </div>
          ),
          thead: ({ children }) => (
            <thead className={`${light ? 'bg-slate-100' : 'bg-white/10'}`}>{children}</thead>
          ),
          th: ({ children }) => (
            <th className={`border ${light ? 'border-slate-200' : 'border-white/20'} px-2 py-1 text-start ${light ? 'text-slate-900' : 'text-white/90'} font-semibold`}>
              {children}
            </th>
          ),
          td: ({ children }) => (
            <td className={`border ${light ? 'border-slate-200' : 'border-white/20'} px-2 py-1 ${light ? 'text-slate-800' : 'text-white/80'}`}>
              {children}
            </td>
          ),
        }}
      >
        {content}
      </ReactMarkdown>
      {isStreaming && (
        <span className="streaming-cursor inline-block w-1.5 h-4 bg-purple-400/90 ms-0.5 align-middle rounded-sm" />
      )}
    </div>
  )
}

export default AIMarkdownRenderer
