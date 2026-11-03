import { axiosOpen } from "@/api/axios";
import type {
  BriefingAcceptedResponse,
  BriefingListResponse,
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

export async function listBriefings(): Promise<BriefingListResponse> {
  const response = await axiosOpen.get<BriefingListResponse>("/briefings");
  return response.data;
}

export async function getBriefing(
  briefingId: string
): Promise<BriefingStatusResponse> {
  const response = await axiosOpen.get<BriefingStatusResponse>(
    `/briefings/${briefingId}`
  );
  return response.data;
}
