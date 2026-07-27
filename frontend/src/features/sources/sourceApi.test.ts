import { describe, expect, it, vi } from 'vitest'
import {
  deleteSourceAsset,
  indexCourseSources,
  listSourceChunks,
  SourceApiError,
  updateSourceEnabled,
} from './sourceApi'

function jsonResponse(payload: unknown, status = 200): Response {
  return new Response(JSON.stringify(payload), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

describe('sourceApi', () => {
  it('encodes source identifiers and bounds chunk previews', async () => {
    const fetchMock = vi
      .fn<typeof fetch>()
      .mockResolvedValue(jsonResponse([]))
    vi.stubGlobal('fetch', fetchMock)

    await listSourceChunks(
      'http://127.0.0.1:8001/',
      'asset:notes/one',
      { limit: 25, offset: 50 },
    )

    expect(fetchMock).toHaveBeenCalledWith(
      'http://127.0.0.1:8001/sources/asset%3Anotes%2Fone/chunks?limit=25&offset=50',
      expect.objectContaining({ method: 'GET' }),
    )
  })

  it('sends source availability and indexing as explicit JSON', async () => {
    const fetchMock = vi
      .fn<typeof fetch>()
      .mockResolvedValueOnce(
        jsonResponse({ id: 'job:one', enabled: false }),
      )
      .mockResolvedValueOnce(
        jsonResponse({
          source_ids: ['job:one'],
          total_sources: 1,
          unavailable_source_ids: [],
          total_chunks: 2,
          embedded_chunks: 2,
          skipped_chunks: 0,
          model: 'local',
          dimension: 3,
        }),
      )
    vi.stubGlobal('fetch', fetchMock)

    await updateSourceEnabled(
      'http://127.0.0.1:8001',
      'job:one',
      false,
    )
    await indexCourseSources(
      'http://127.0.0.1:8001',
      'course one',
      ['job:one'],
    )

    expect(fetchMock.mock.calls[0]?.[1]).toEqual(
      expect.objectContaining({
        method: 'PATCH',
        body: JSON.stringify({ enabled: false }),
      }),
    )
    expect(fetchMock.mock.calls[1]?.[0]).toBe(
      'http://127.0.0.1:8001/courses/course%20one/sources/index',
    )
    expect(fetchMock.mock.calls[1]?.[1]).toEqual(
      expect.objectContaining({
        method: 'POST',
        body: JSON.stringify({ source_ids: ['job:one'] }),
      }),
    )
  })

  it('accepts a successful empty delete response', async () => {
    const fetchMock = vi
      .fn<typeof fetch>()
      .mockResolvedValue(new Response(null, { status: 204 }))
    vi.stubGlobal('fetch', fetchMock)

    await expect(
      deleteSourceAsset(
        'http://127.0.0.1:8001',
        'asset/one',
      ),
    ).resolves.toBeUndefined()
  })

  it('surfaces the backend detail without losing the status', async () => {
    vi.stubGlobal(
      'fetch',
      vi
        .fn<typeof fetch>()
        .mockResolvedValue(
          jsonResponse({ detail: 'Index model is unavailable.' }, 503),
        ),
    )

    await expect(
      indexCourseSources(
        'http://127.0.0.1:8001',
        'course',
        ['job:one'],
      ),
    ).rejects.toEqual(
      expect.objectContaining<Partial<SourceApiError>>({
        message: 'Index model is unavailable.',
        status: 503,
      }),
    )
  })
})
