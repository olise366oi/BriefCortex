import { axiosOpen } from "@/api/axios";
import type {
  BriefingAcceptedResponse,
  BriefingRequest,
  BriefingStatusResponse,
} from "@/types";

export async function createBriefing(
  payload: BriefingRequest
): Promise<BriefingAcceptedResponse> {
  const response = await axiosOpen.post<BriefingAcceptedResponse>(
    "/briefings",
    payload
  );
  return response.data;
}

export async function getBriefingStatus(
  requestId: string
): Promise<BriefingStatusResponse> {
  const response = await axiosOpen.get<BriefingStatusResponse>(
    `/briefings/${requestId}`
  );
  return response.data;
}
