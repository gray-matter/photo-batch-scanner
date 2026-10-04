/** Check an HTTP response without requiring a success body for write actions. */
export async function checkedRequest(url, options = {}, {
  errorMessage = "Request failed", detail = false, checkStatus = true,
} = {}) {
  const response = await fetch(url, options);
  if (checkStatus && !response.ok) {
    let message = errorMessage;
    if (detail) {
      const error = await response.json().catch(() => ({}));
      if (detail === "string") {
        if (typeof error.detail === "string") message = error.detail;
      } else {
        message = error.detail || errorMessage;
      }
    }
    throw new Error(message);
  }
  return response;
}

export async function requestJSON(url, options = {}, policy = {}) {
  const response = await checkedRequest(url, options, policy);
  return response.json();
}

/** Manual refreshes and interval ticks share the same in-flight guard. */
export function createPolling(refresh, intervalMs) {
  let active = false;
  let timer = null;

  async function guardedRefresh() {
    if (active) return;
    active = true;
    try {
      await refresh();
    } finally {
      active = false;
    }
  }

  return {
    refresh: guardedRefresh,
    start() {
      if (timer === null) timer = setInterval(guardedRefresh, intervalMs);
    },
    stop() {
      if (timer !== null) clearInterval(timer);
      timer = null;
    },
  };
}
