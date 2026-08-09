import type { SourceLocator } from '../sources/sourceTypes'

export type CitationTextSegment = {
  text: string
  isMatch: boolean
}

function locatorNumber(
  locator: SourceLocator,
  field: string,
): number | null {
  const value = (locator as unknown as Record<string, unknown>)[field]
  return typeof value === 'number' && Number.isFinite(value)
    ? value
    : null
}

function unsupportedLocatorLabel(locator: SourceLocator): string {
  const version = Number.isFinite(locator.schema_version)
    ? locator.schema_version
    : '?'
  return `Unsupported locator v${version}`
}

export function formatCitationTime(seconds: number): string {
  const wholeSeconds = Math.max(0, Math.floor(seconds))
  const hours = Math.floor(wholeSeconds / 3600)
  const minutes = Math.floor((wholeSeconds % 3600) / 60)
  const remainder = String(wholeSeconds % 60).padStart(2, '0')
  if (hours > 0) {
    return `${hours}:${String(minutes).padStart(2, '0')}:${remainder}`
  }
  return `${minutes}:${remainder}`
}

export function formatSourceLocator(locator: SourceLocator): string {
  if (locator.schema_version !== 1) {
    return unsupportedLocatorLabel(locator)
  }
  switch (locator.kind) {
    case 'video_time': {
      const startSeconds = locatorNumber(locator, 'start_seconds')
      const endSeconds = locatorNumber(locator, 'end_seconds')
      if (startSeconds === null || endSeconds === null) {
        return unsupportedLocatorLabel(locator)
      }
      const start = formatCitationTime(startSeconds)
      const end = formatCitationTime(endSeconds)
      return start === end ? start : `${start}–${end}`
    }
    case 'pdf_page': {
      const page = locatorNumber(locator, 'page_number')
      return page === null
        ? unsupportedLocatorLabel(locator)
        : `Page ${page}`
    }
    case 'ppt_slide': {
      const slide = locatorNumber(locator, 'slide_number')
      return slide === null
        ? unsupportedLocatorLabel(locator)
        : `Slide ${slide}`
    }
    case 'docx_paragraph': {
      const paragraph = locatorNumber(locator, 'paragraph_number')
      return paragraph === null
        ? unsupportedLocatorLabel(locator)
        : `Paragraph ${paragraph}`
    }
    case 'text_section': {
      const section = locatorNumber(locator, 'section_number')
      return section === null
        ? unsupportedLocatorLabel(locator)
        : `Section ${section}`
    }
    case 'note_section': {
      const section = locatorNumber(locator, 'section_number')
      return section === null
        ? unsupportedLocatorLabel(locator)
        : `Note section ${section}`
    }
    default:
      return unsupportedLocatorLabel(locator)
  }
}

export function resolveCitationMediaUrl(
  apiBaseUrl: string,
  mediaUrl: string | null,
  courseId: string,
  citationId: string,
): string | null {
  const expectedPath = [
    '',
    'courses',
    encodeURIComponent(courseId),
    'chat',
    'citations',
    encodeURIComponent(citationId),
    'content',
  ].join('/')
  return resolveApiMediaUrl(apiBaseUrl, mediaUrl, expectedPath)
}

export function resolveApiMediaUrl(
  apiBaseUrl: string,
  mediaUrl: string | null,
  expectedPath: string,
): string | null {
  if (!mediaUrl) return null
  try {
    const apiBase = new URL(apiBaseUrl)
    if (apiBase.protocol !== 'http:' && apiBase.protocol !== 'https:') {
      return null
    }
    const normalizedBase = `${apiBase.toString().replace(/\/+$/, '')}/`
    const resolved = new URL(mediaUrl, normalizedBase)
    if (
      resolved.protocol !== 'http:' &&
      resolved.protocol !== 'https:'
    ) {
      return null
    }
    if (
      resolved.origin !== apiBase.origin ||
      resolved.pathname !== expectedPath ||
      resolved.search ||
      resolved.hash ||
      resolved.username ||
      resolved.password
    ) {
      return null
    }
    return resolved.toString()
  } catch {
    return null
  }
}

export function pdfUrlAtPage(
  mediaUrl: string,
  locator: SourceLocator,
): string {
  if (
    locator.schema_version !== 1 ||
    locator.kind !== 'pdf_page'
  ) {
    return mediaUrl
  }
  const page = locatorNumber(locator, 'page_number')
  if (page === null) return mediaUrl
  try {
    const url = new URL(mediaUrl)
    url.hash = `page=${page}`
    return url.toString()
  } catch {
    const withoutHash = mediaUrl.split('#', 1)[0]
    return `${withoutHash}#page=${page}`
  }
}

export function seekMediaToLocator(
  media: Pick<HTMLMediaElement, 'currentTime'>,
  locator: SourceLocator,
): boolean {
  if (
    locator.schema_version !== 1 ||
    locator.kind !== 'video_time'
  ) {
    return false
  }
  const start = locatorNumber(locator, 'start_seconds')
  if (start === null) return false
  media.currentTime = Math.max(0, start)
  return true
}

export function segmentCitationQuote(
  text: string,
  quote: string,
): CitationTextSegment[] {
  const needle = quote.trim()
  if (!text || !needle) {
    return [{ text, isMatch: false }]
  }
  const index = text.indexOf(needle)
  if (index < 0) {
    return [{ text, isMatch: false }]
  }
  const segments: CitationTextSegment[] = []
  if (index > 0) {
    segments.push({
      text: text.slice(0, index),
      isMatch: false,
    })
  }
  segments.push({ text: needle, isMatch: true })
  const matchEnd = index + needle.length
  if (matchEnd < text.length) {
    segments.push({
      text: text.slice(matchEnd),
      isMatch: false,
    })
  }
  return segments
}

export function restoreCitationFocus(
  element: HTMLElement | null,
): boolean {
  if (!element || !element.isConnected) return false
  element.focus()
  return document.activeElement === element
}
