import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { uploadArtifact, type Artifact } from "@/api/fleet";
import { ApiError } from "@/api/http";
import { ArtifactUploadDialog } from "@/components/artifacts/ArtifactUploadDialog";

vi.mock("@/api/fleet", () => ({ uploadArtifact: vi.fn() }));

beforeEach(() => vi.mocked(uploadArtifact).mockReset());
afterEach(() => vi.restoreAllMocks());

function setup() {
  const onClose = vi.fn();
  const onUploaded = vi.fn();
  const view = render(
    <ArtifactUploadDialog
      token="token"
      organizationId="org-1"
      deviceTypes={[]}
      onClose={onClose}
      onUploaded={onUploaded}
    />,
  );
  return { ...view, onClose, onUploaded, user: userEvent.setup() };
}

async function beginUpload(user: ReturnType<typeof userEvent.setup>) {
  await user.upload(screen.getByLabelText("File"), new File(["image"], "disk.img"));
  await user.type(screen.getByLabelText("Version"), "1.0");
  await user.click(screen.getByRole("button", { name: "Upload" }));
}

describe("ArtifactUploadDialog", () => {
  it("requires a file before calling the upload API", async () => {
    const { user } = setup();
    await user.type(screen.getByLabelText("Name"), "OS");
    await user.type(screen.getByLabelText("Version"), "1.0");
    await user.click(screen.getByRole("button", { name: "Upload" }));
    expect(screen.getByText("Choose a file to upload.")).toBeInTheDocument();
    expect(uploadArtifact).not.toHaveBeenCalled();
  });

  it("preserves an edited name when another file is selected", async () => {
    const { user } = setup();
    await user.upload(screen.getByLabelText("File"), new File(["image"], "disk.release.img"));
    expect(screen.getByLabelText("Name")).toHaveValue("disk.release");
    await user.clear(screen.getByLabelText("Name"));
    await user.type(screen.getByLabelText("Name"), "Custom OS");
    await user.upload(screen.getByLabelText("File"), new File(["other"], "other.img"));
    expect(screen.getByLabelText("Name")).toHaveValue("Custom OS");
  });

  it.each([
    [new ApiError(503, "storage_unavailable", "Storage is unavailable"), "Storage is unavailable"],
    [new Error("unexpected failure"), "Upload failed."],
  ])("restores the form after a failure and permits retry", async (error, message) => {
    vi.mocked(uploadArtifact).mockRejectedValueOnce(error);
    const { user, onUploaded } = setup();
    await beginUpload(user);
    expect(await screen.findByText(message)).toBeInTheDocument();
    expect(screen.getByLabelText("Name")).toBeEnabled();
    expect(screen.queryByRole("progressbar")).not.toBeInTheDocument();
    expect(onUploaded).not.toHaveBeenCalled();
    const artifact = { id: "artifact-1" } as Artifact;
    vi.mocked(uploadArtifact).mockResolvedValueOnce(artifact);
    await user.click(screen.getByRole("button", { name: "Upload" }));
    await waitFor(() => expect(onUploaded).toHaveBeenCalledWith(artifact));
    expect(uploadArtifact).toHaveBeenCalledTimes(2);
    expect(screen.queryByText(message)).not.toBeInTheDocument();
  });

  it("keeps the upload active when cancellation is declined and aborts on unmount", async () => {
    vi.mocked(uploadArtifact).mockImplementation(() => new Promise(() => {}));
    vi.spyOn(window, "confirm").mockReturnValue(false);
    const { user, onClose, unmount } = setup();
    await beginUpload(user);
    const signal = vi.mocked(uploadArtifact).mock.calls[0][3]?.signal;
    expect(signal?.aborted).toBe(false);
    const during = new Event("beforeunload", { cancelable: true });
    fireEvent(window, during);
    expect(during.defaultPrevented).toBe(true);
    await user.click(screen.getByRole("button", { name: "Cancel upload" }));
    expect(window.confirm).toHaveBeenCalledOnce();
    expect(signal?.aborted).toBe(false);
    expect(onClose).not.toHaveBeenCalled();
    expect(screen.getByLabelText("File")).toBeDisabled();
    unmount();
    expect(signal?.aborted).toBe(true);
    const after = new Event("beforeunload", { cancelable: true });
    fireEvent(window, after);
    expect(after.defaultPrevented).toBe(false);
  });

  it("silently handles cancellation and removes the unload warning", async () => {
    let rejectUpload!: (reason: ApiError) => void;
    vi.mocked(uploadArtifact).mockImplementation(
      () =>
        new Promise((_, reject) => {
          rejectUpload = reject;
        }),
    );
    const { user, onUploaded } = setup();
    await beginUpload(user);
    await act(async () => rejectUpload(new ApiError(0, "aborted", "The upload was cancelled.")));
    expect(screen.queryByText("The upload was cancelled.")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Upload" })).toBeEnabled();
    expect(onUploaded).not.toHaveBeenCalled();
    const event = new Event("beforeunload", { cancelable: true });
    fireEvent(window, event);
    expect(event.defaultPrevented).toBe(false);
  });
});
