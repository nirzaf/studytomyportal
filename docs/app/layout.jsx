import { Wix_Madefor_Text } from 'next/font/google'
import '../styles.css'
import CustomNavbar from '../components/Navbar/Navbar'
import SectionMain from '../components/site/SectionMain'
import CustomFooter from '../components/Footer/Footer'
import PostHogProvider from '../components/Analytics/PostHogProvider'

const docsSiteUrl = process.env.NEXT_PUBLIC_DOCS_URL

export const metadata = {
  title: {
    default: 'Studytomy Portal Docs',
    template: '%s – Studytomy Portal Docs',
  },
  description:
    'Documentation for Studytomy Portal, including self-hosting, course workflows, AI features, and API reference.',
  keywords: [
    'Studytomy Portal',
    'open source LMS',
    'learning management system',
    'self-hosted LMS',
    'course creation',
    'Studytomy documentation',
    'Studytomy Portal docs',
  ],
  ...(docsSiteUrl ? { metadataBase: new URL(docsSiteUrl) } : {}),
  robots: {
    index: true,
    follow: true,
    googleBot: {
      index: true,
      follow: true,
      'max-video-preview': -1,
      'max-image-preview': 'large',
      'max-snippet': -1,
    },
  },
  openGraph: {
    type: 'website',
    locale: 'en_US',
    ...(docsSiteUrl ? { url: docsSiteUrl } : {}),
    siteName: 'Studytomy Portal Docs',
    description:
      'Documentation for Studytomy Portal, including self-hosting, course workflows, AI features, and API reference.',
    ...(docsSiteUrl ? { images: [{ url: '/og', alt: 'Studytomy Portal Docs', width: 1200, height: 630 }] } : {}),
  },
  twitter: {
    card: 'summary_large_image',
  },
  icons: {
    icon: [
      { url: '/studytomy-mini.png', sizes: '87x87', type: 'image/png' },
    ],
    apple: '/studytomy-mini.png',
  },
  manifest: '/favicons/site.webmanifest',
}

// Self-hosted and preloaded by Next: no request to Google, no render-blocking CSS.
const wix = Wix_Madefor_Text({
  subsets: ['latin'],
  display: 'swap',
  variable: '--font-wix',
})

export default function RootLayout({ children }) {
  return (
    <html lang="en" dir="ltr" className={wix.variable} suppressHydrationWarning>
      <body>
        <PostHogProvider>
          <CustomNavbar />
          <SectionMain>{children}</SectionMain>
          <CustomFooter />
        </PostHogProvider>
      </body>
    </html>
  )
}
