import { describe, expect, it, vi } from 'vitest'
import {
  confirmInternalNavigation,
  UNPROTECTED_NAVIGATION_MESSAGE,
} from './navigationGuard'

describe('confirmInternalNavigation', () => {
  it('continues without prompting when every change is durable', () => {
    const confirmLeave = vi.fn()

    expect(
      confirmInternalNavigation(false, confirmLeave),
    ).toBe(true)
    expect(confirmLeave).not.toHaveBeenCalled()
  })

  it('keeps the user in place when an unprotected change is rejected', () => {
    const confirmLeave = vi.fn().mockReturnValue(false)

    expect(
      confirmInternalNavigation(true, confirmLeave),
    ).toBe(false)
    expect(confirmLeave).toHaveBeenCalledWith(
      UNPROTECTED_NAVIGATION_MESSAGE,
    )
  })

  it('allows an explicit decision to leave', () => {
    const confirmLeave = vi.fn().mockReturnValue(true)

    expect(
      confirmInternalNavigation(true, confirmLeave),
    ).toBe(true)
  })
})
