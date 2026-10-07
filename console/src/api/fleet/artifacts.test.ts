import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  deleteArtifact,
  downloadArtifact,
  listArtifacts,
  uploadArtifact,
} from "@/api/fleet/artifacts";
import { ApiError, apiRequest } from "@/api/http";

vi.mock("@/api/http", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/api/http")>()),
  apiRequest: vi.fn(),
}));
vi.mock("@/lib/apiBase", () => ({ resolveApiBaseUrl: () => "https://api.example.test" }));

// Only emulate the browser transport; the real API wrapper builds the form,
// handles events, and translates the responses under test.
class UploadRequest {
  open = vi.fn();
  setRequestHeader = vi.fn();
  send = vi.fn<(body: FormData) => void>();
  abort = vi.fn(() => this.onabort?.());
  upload: { onprogress?: (event: ProgressEvent) => void } = {};
  onload?: () => void;
  onerror?: () => void;
  onabort?: () => void;
  status = 201;
  responseText = '{"id":"artifact-1"}';
}

const payload = {
  file: new File(["image bytes"], "disk.img", { type: "application/octet-stream" }),
  name: "Meteor OS",
  version: "1.0",
  type: "os_image" as const,
};

let xhr: UploadRequest;

beforeEach(() => {
  vi.clearAllMocks();
  xhr = new UploadRequest();
  vi.stubGlobal(
    "XMLHttpRequest",
    class {
      constructor() {
        return xhr;
      }
    },
  );
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("artifact requests", () => {
  it("encodes filters and pagination while omitting empty values", async () => {
    vi.mocked(apiRequest).mockResolvedValue({ items: [], total: 0 });
    await listArtifacts("token", "org-1", {
      search: "OS & firmware/+",
      version: "",
      device_type_id: undefined,
      type: "os_image",
      page: 2,
      page_size: 10,
    });
    const [path, options] = vi.mocked(apiRequest).mock.calls[0];
    const url = new URL(path, "https://api.example.test");
    expect(url.pathname).toBe("/api/v1/organizations/org-1/artifacts");
    expect(Object.fromEntries(url.searchParams)).toEqual({
      search: "OS & firmware/+",
      type: "os_image",
      page: "2",
      page_size: "10",
    });
    expect(options).toEqual({ token: "token" });
  });

  it("does not append a query marker when there are no filters", async () => {
    await listArtifacts("token", "org-1");
    expect(apiRequest).toHaveBeenCalledWith("/api/v1/organizations/org-1/artifacts", {
      token: "token",
    });
  });

  it("deletes through the organization-scoped endpoint and propagates errors", async () => {
    const error = new ApiError(403, "insufficient_permission", "Forbidden");
    vi.mocked(apiRequest).mockRejectedValueOnce(error);
    await expect(deleteArtifact("token", "org-1", "artifact-1")).rejects.toBe(error);
    expect(apiRequest).toHaveBeenCalledWith("/api/v1/organizations/org-1/artifacts/artifact-1", {
      method: "DELETE",
      token: "token",
    });
  });
});

describe("uploadArtifact", () => {
  it("sends multipart data with auth and resolves only after the server responds", async () => {
    const progress = vi.fn();
    const settled = vi.fn();
    const promise = uploadArtifact(
      "token",
      "org-1",
      {
        ...payload,
        description: "Stable release",
        device_type_id: "type-1",
      },
      { onProgress: progress },
    );
    void promise.then(settled);
    expect(xhr.open).toHaveBeenCalledWith(
      "POST",
      "https://api.example.test/api/v1/organizations/org-1/artifacts",
    );
    expect(xhr.setRequestHeader.mock.calls).toEqual([
      ["Authorization", "Bearer token"],
      ["Accept", "application/json"],
    ]);
    const form = xhr.send.mock.calls[0][0];
    expect(form.get("name")).toBe("Meteor OS");
    expect(form.get("version")).toBe("1.0");
    expect(form.get("type")).toBe("os_image");
    expect(form.get("description")).toBe("Stable release");
    expect(form.get("device_type_id")).toBe("type-1");
    expect(form.get("file")).toBeInstanceOf(File);
    expect((form.get("file") as File).name).toBe("disk.img");
    expect((form.get("file") as File).size).toBe(payload.file.size);
    xhr.upload.onprogress?.(
      new ProgressEvent("progress", { loaded: 100, total: 100, lengthComputable: true }),
    );
    await Promise.resolve();
    expect(progress).toHaveBeenCalledWith({ loaded: 100, total: 100 });
    expect(settled).not.toHaveBeenCalled();
    xhr.onload?.();
    await expect(promise).resolves.toEqual({ id: "artifact-1" });
  });

  it("omits optional empty fields and ignores indeterminate progress", async () => {
    const progress = vi.fn();
    const promise = uploadArtifact(
      "token",
      "org-1",
      {
        ...payload,
        description: "",
        device_type_id: null,
      },
      { onProgress: progress },
    );
    const form = xhr.send.mock.calls[0][0];
    expect(form.has("description")).toBe(false);
    expect(form.has("device_type_id")).toBe(false);
    xhr.upload.onprogress?.(new ProgressEvent("progress", { loaded: 50 }));
    expect(progress).not.toHaveBeenCalled();
    xhr.onload?.();
    await promise;
  });

  it.each([
    [
      409,
      '{"error":{"code":"artifact_exists","message":"Already exists"}}',
      "artifact_exists",
      "Already exists",
    ],
    [503, "<html>unavailable</html>", "request_failed", "Upload failed with status 503"],
    [413, "", "request_failed", "Upload failed with status 413"],
  ])("translates HTTP %i errors", async (status, body, code, message) => {
    const promise = uploadArtifact("token", "org-1", payload);
    xhr.status = status;
    xhr.responseText = body;
    xhr.onload?.();
    await expect(promise).rejects.toMatchObject({ status, code, message });
  });

  it("reports transport errors", async () => {
    const promise = uploadArtifact("token", "org-1", payload);
    xhr.onerror?.();
    await expect(promise).rejects.toMatchObject({ status: 0, code: "network_error" });
  });

  it("does not send a request for an already aborted signal", async () => {
    const controller = new AbortController();
    controller.abort();
    await expect(
      uploadArtifact("token", "org-1", payload, { signal: controller.signal }),
    ).rejects.toMatchObject({ code: "aborted" });
    expect(xhr.send).not.toHaveBeenCalled();
  });

  it("aborts an active transport and rejects the upload", async () => {
    const controller = new AbortController();
    const promise = uploadArtifact("token", "org-1", payload, { signal: controller.signal });
    controller.abort();
    expect(xhr.abort).toHaveBeenCalledOnce();
    await expect(promise).rejects.toMatchObject({ status: 0, code: "aborted" });
  });
});

describe("downloadArtifact", () => {
  it("submits the ticket in a POST body and removes the temporary form", async () => {
    vi.mocked(apiRequest).mockResolvedValueOnce({
      url: "/api/v1/organizations/org-1/artifacts/artifact-1/download",
      ticket: "download-ticket",
      expires_at: "2026-10-07T12:00:00Z",
    });
    const submit = vi.spyOn(HTMLFormElement.prototype, "submit").mockImplementation(function (
      this: HTMLFormElement,
    ) {
      expect(this.isConnected).toBe(true);
      expect(this.method).toBe("post");
      expect(this.action).toBe(
        "https://api.example.test/api/v1/organizations/org-1/artifacts/artifact-1/download",
      );
      expect(new FormData(this).get("ticket")).toBe("download-ticket");
      expect(this.querySelector("input")?.type).toBe("hidden");
    });
    await downloadArtifact("token", "org-1", "artifact-1");
    expect(apiRequest).toHaveBeenCalledWith(
      "/api/v1/organizations/org-1/artifacts/artifact-1/download-link",
      {
        method: "POST",
        token: "token",
      },
    );
    expect(submit).toHaveBeenCalledOnce();
    expect(document.querySelector("form")).toBeNull();
  });

  it("does not submit a form if ticket creation fails", async () => {
    const error = new ApiError(404, "artifact_not_found", "Missing");
    vi.mocked(apiRequest).mockRejectedValueOnce(error);
    const submit = vi.spyOn(HTMLFormElement.prototype, "submit");
    await expect(downloadArtifact("token", "org-1", "missing")).rejects.toBe(error);
    expect(submit).not.toHaveBeenCalled();
    expect(document.querySelector("form")).toBeNull();
  });
});
