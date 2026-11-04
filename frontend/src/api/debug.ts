import { axiosOpen } from "@/api/axios";
import type { DebugRunRequest, DebugRunResponse } from "@/types";

/**
 * Development-only endpoint. The backend mounts it under /debug when DEBUG is
 * on, so a build against a release backend will 404 here and that is expected.
 */
export async function runDebug(
  payload: DebugRunRequest
): Promise<DebugRunResponse> {
  const response = await axiosOpen.post<DebugRunResponse>("/debug/run", payload, {
    // A full run takes minutes: retrieval plus the model call.
    timeout: 300_000,
  });
  return response.data;
}
