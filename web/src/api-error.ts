export class PmoHttpError extends Error {
  constructor(readonly status: number, readonly code: string, message: string) {
    super(message)
  }
}

export function isRejectedWrite(error: unknown): error is PmoHttpError {
  return error instanceof PmoHttpError && [401, 403, 404, 409, 422].includes(error.status)
    && error.code !== 'REQUEST_IN_PROGRESS'
}
