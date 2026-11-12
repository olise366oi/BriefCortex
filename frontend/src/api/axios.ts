import axios, { AxiosInstance } from "axios";

import { envConfig } from "@/lib/config/envConfig";

const createAxiosInstance = (baseURL: string): AxiosInstance =>
  axios.create({
    baseURL,
    // A hung request must fail so the poll loop can retry it; without a timeout
    // the UI would sit in "loading" for as long as the socket stays open.
    timeout: 20_000,
    headers: {
      "Content-Type": "application/json",
      Accept: "application/json",
    },
  });

export const axiosOpen = createAxiosInstance(envConfig.API_BASE_URL);
