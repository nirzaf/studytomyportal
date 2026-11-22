'use client'

import { usePathname } from 'next/navigation'
import Link from 'next/link'
import { track } from '../Analytics/PostHogProvider'
import { ctaUrl } from '@/lib/cta'

/**
 * A docs navigation link with UTM tags and click tracking.
 */
export default function CtaLink({ href, placement, term, children, ...props }) {
  const route = usePathname() || '/'
  const url = ctaUrl(href, { placement, route, term })
  const onClick = (event) => {
    track('docs_cta_click', { placement, destination: href, page: route, term })
    props.onClick?.(event)
  }
  if (href.startsWith('/')) {
    return <Link {...props} href={url} onClick={onClick}>{children}</Link>
  }
  return (
    <a
      {...props}
      href={url}
      onClick={onClick}
    >
      {children}
    </a>
  )
}
