"use client";

import { useEffect, useRef, useState } from "react";
import { toast } from "sonner";

import {
  deleteAzureBlobStorageApiV1OrganizationsStorageAzureBlobDelete,
  getAzureBlobStorageApiV1OrganizationsStorageAzureBlobGet,
  saveAzureBlobStorageApiV1OrganizationsStorageAzureBlobPost,
  testAzureBlobStorageApiV1OrganizationsStorageAzureBlobTestPost,
} from "@/client/sdk.gen";
import type { AzureBlobStorageResponse } from "@/client/types.gen";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { detailFromError } from "@/lib/apiError";
import { useAuth } from "@/lib/auth";

const EMPTY: AzureBlobStorageResponse = {
  enabled: false,
  connection_string: "",
  account_name: "",
  account_url: "",
  account_key: "",
  container: "voice-audio",
  configured: false,
};

export function AzureBlobStorageSection() {
  const { user, loading: authLoading } = useAuth();
  const [settings, setSettings] = useState<AzureBlobStorageResponse>(EMPTY);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [testing, setTesting] = useState(false);
  const hasFetched = useRef(false);

  useEffect(() => {
    if (authLoading || !user || hasFetched.current) {
      return;
    }
    hasFetched.current = true;
    fetchSettings();
  }, [authLoading, user]);

  async function fetchSettings() {
    try {
      const response = await getAzureBlobStorageApiV1OrganizationsStorageAzureBlobGet();
      if (response.error) {
        throw new Error(detailFromError(response.error, "Failed to load Azure Blob Storage settings"));
      }
      if (response.data) {
        setSettings(response.data);
      }
    } catch {
      // Not configured yet — that's fine
    } finally {
      setLoading(false);
    }
  }

  async function handleSave(e: React.FormEvent) {
    e.preventDefault();
    setSaving(true);
    try {
      const response = await saveAzureBlobStorageApiV1OrganizationsStorageAzureBlobPost({
        body: {
          enabled: settings.enabled,
          connection_string: settings.connection_string ?? "",
          account_name: settings.account_name ?? "",
          account_url: settings.account_url ?? "",
          account_key: settings.account_key ?? "",
          container: settings.container ?? "voice-audio",
        },
      });
      if (response.error) {
        throw new Error(detailFromError(response.error, "Failed to save"));
      }
      toast.success("Azure Blob Storage settings saved");
      await fetchSettings();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Failed to save Azure Blob Storage settings");
    } finally {
      setSaving(false);
    }
  }

  async function handleTest() {
    setTesting(true);
    try {
      const response = await testAzureBlobStorageApiV1OrganizationsStorageAzureBlobTestPost({
        body: {
          connection_string: settings.connection_string ?? "",
          account_name: settings.account_name ?? "",
          account_url: settings.account_url ?? "",
          account_key: settings.account_key ?? "",
          container: settings.container ?? "voice-audio",
        },
      });
      if (response.error) {
        throw new Error(detailFromError(response.error, "Connection test failed"));
      }
      toast.success(
        `Connected to container '${response.data?.container ?? settings.container}'` +
          (response.data?.account ? ` on ${response.data.account}` : "")
      );
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Connection test failed");
    } finally {
      setTesting(false);
    }
  }

  async function handleDelete() {
    setSaving(true);
    try {
      const response = await deleteAzureBlobStorageApiV1OrganizationsStorageAzureBlobDelete();
      if (response.error) {
        throw new Error(detailFromError(response.error, "Failed to remove"));
      }
      setSettings(EMPTY);
      toast.success("Azure Blob Storage settings removed");
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Failed to remove Azure Blob Storage settings");
    } finally {
      setSaving(false);
    }
  }

  if (loading) {
    return <p className="text-sm text-muted-foreground">Loading...</p>;
  }

  return (
    <form onSubmit={handleSave} className="space-y-4">
      <p className="text-sm text-muted-foreground">
        Store call recordings and transcripts in your own Azure Blob Storage
        container. When enabled, new call artifacts land in Azure instead of
        the default storage.
      </p>
      <div className="flex items-center gap-2">
        <input
          id="azure-blob-enabled"
          type="checkbox"
          className="h-4 w-4"
          checked={settings.enabled}
          onChange={(e) => setSettings({ ...settings, enabled: e.target.checked })}
        />
        <Label htmlFor="azure-blob-enabled">Store recordings in Azure Blob Storage</Label>
      </div>
      <div className="space-y-2">
        <Label htmlFor="azure-connection-string">Connection String</Label>
        <Input
          id="azure-connection-string"
          type="password"
          placeholder="DefaultEndpointsProtocol=https;AccountName=...;..."
          value={settings.connection_string}
          onChange={(e) => setSettings({ ...settings, connection_string: e.target.value })}
          autoComplete="off"
        />
        <p className="text-xs text-muted-foreground">
          Find it in the Azure portal under Storage account → Access keys. Alternatively,
          fill in the account fields below instead.
        </p>
      </div>
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
        <div className="space-y-2">
          <Label htmlFor="azure-account-name">Account Name</Label>
          <Input
            id="azure-account-name"
            placeholder="mystorageaccount"
            value={settings.account_name}
            onChange={(e) => setSettings({ ...settings, account_name: e.target.value })}
            autoComplete="off"
          />
        </div>
        <div className="space-y-2">
          <Label htmlFor="azure-account-key">Account Key</Label>
          <Input
            id="azure-account-key"
            type="password"
            placeholder="••••"
            value={settings.account_key}
            onChange={(e) => setSettings({ ...settings, account_key: e.target.value })}
            autoComplete="off"
          />
        </div>
      </div>
      <div className="space-y-2">
        <Label htmlFor="azure-account-url">Account URL (optional)</Label>
        <Input
          id="azure-account-url"
          placeholder="https://mystorageaccount.blob.core.windows.net"
          value={settings.account_url}
          onChange={(e) => setSettings({ ...settings, account_url: e.target.value })}
          autoComplete="off"
        />
      </div>
      <div className="space-y-2">
        <Label htmlFor="azure-container">Container</Label>
        <Input
          id="azure-container"
          placeholder="voice-audio"
          value={settings.container}
          onChange={(e) => setSettings({ ...settings, container: e.target.value })}
          required
        />
      </div>
      <div className="flex flex-wrap gap-2">
        <Button type="submit" disabled={saving || testing}>
          {saving ? "Saving..." : "Save"}
        </Button>
        <Button type="button" variant="secondary" disabled={saving || testing} onClick={handleTest}>
          {testing ? "Testing..." : "Test connection"}
        </Button>
        {settings.configured && (
          <Button type="button" variant="destructive" disabled={saving || testing} onClick={handleDelete}>
            Remove
          </Button>
        )}
      </div>
    </form>
  );
}
