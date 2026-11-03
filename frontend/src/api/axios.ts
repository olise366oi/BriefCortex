import axios, { AxiosInstance } from "axios";

import { envConfig } from "@/lib/config/envConfig";

const createAxiosInstance = (baseURL: string): AxiosInstance =>
  axios.create({
    baseURL,
    headers: {
      "Content-Type": "application/json",
      Accept: "application/json",
    },
  });

export const axiosOpen = createAxiosInstance(envConfig.API_BASE_URL);
