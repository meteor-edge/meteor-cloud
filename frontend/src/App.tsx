import { Navigate, Route, Routes } from "react-router-dom";

import { ProtectedRoute } from "@/auth/ProtectedRoute";
import { AppLayout } from "@/layouts/AppLayout";
import { AuthLayout } from "@/layouts/AuthLayout";
import { LoginPage } from "@/pages/auth/LoginPage";
import { RegisterPage } from "@/pages/auth/RegisterPage";
import { HealthPage } from "@/pages/HealthPage";
import { LandingPage } from "@/pages/LandingPage";
import { DeviceDetailPage } from "@/pages/fleet/DeviceDetailPage";
import { DeviceGroupsPage } from "@/pages/fleet/DeviceGroupsPage";
import { DevicesPage } from "@/pages/fleet/DevicesPage";
import { DeviceTypesPage } from "@/pages/fleet/DeviceTypesPage";
import { ApiKeysPage } from "@/pages/fleet/ApiKeysPage";
import { MqttTestPage } from "@/pages/fleet/MqttTestPage";
import { OrganizationCreatePage } from "@/pages/organizations/OrganizationCreatePage";
import { OrganizationListPage } from "@/pages/organizations/OrganizationListPage";
import { OrganizationMembersPage } from "@/pages/organizations/OrganizationMembersPage";
import { OrganizationOverviewPage } from "@/pages/organizations/OrganizationOverviewPage";
import { OrganizationSettingsPage } from "@/pages/organizations/OrganizationSettingsPage";

export function App() {
  return (
    <Routes>
      <Route element={<AuthLayout />}>
        <Route path="login" element={<LoginPage />} />
        <Route path="register" element={<RegisterPage />} />
      </Route>

      <Route element={<AppLayout />}>
        <Route index element={<LandingPage />} />
        <Route path="health" element={<HealthPage />} />
        <Route
          path="organizations"
          element={
            <ProtectedRoute>
              <OrganizationListPage />
            </ProtectedRoute>
          }
        />
        <Route
          path="organizations/new"
          element={
            <ProtectedRoute>
              <OrganizationCreatePage />
            </ProtectedRoute>
          }
        />
        <Route
          path="organizations/:organizationId"
          element={
            <ProtectedRoute>
              <OrganizationOverviewPage />
            </ProtectedRoute>
          }
        />
        <Route
          path="organizations/:organizationId/members"
          element={
            <ProtectedRoute>
              <OrganizationMembersPage />
            </ProtectedRoute>
          }
        />
        <Route
          path="organizations/:organizationId/settings"
          element={
            <ProtectedRoute>
              <OrganizationSettingsPage />
            </ProtectedRoute>
          }
        />
        <Route
          path="organizations/:organizationId/devices"
          element={
            <ProtectedRoute>
              <DevicesPage />
            </ProtectedRoute>
          }
        />
        <Route
          path="organizations/:organizationId/devices/:deviceId"
          element={
            <ProtectedRoute>
              <DeviceDetailPage />
            </ProtectedRoute>
          }
        />
        <Route
          path="organizations/:organizationId/device-types"
          element={
            <ProtectedRoute>
              <DeviceTypesPage />
            </ProtectedRoute>
          }
        />
        <Route
          path="organizations/:organizationId/device-groups"
          element={
            <ProtectedRoute>
              <DeviceGroupsPage />
            </ProtectedRoute>
          }
        />
        <Route
          path="organizations/:organizationId/mqtt"
          element={
            <ProtectedRoute>
              <MqttTestPage />
            </ProtectedRoute>
          }
        />
        <Route
          path="organizations/:organizationId/api-keys"
          element={
            <ProtectedRoute>
              <ApiKeysPage />
            </ProtectedRoute>
          }
        />
        <Route
          path="organizations/:organizationId/enrollment"
          element={<Navigate to="../api-keys" replace />}
        />
        <Route
          path="organizations/:organizationId/registration-tokens"
          element={
            <Navigate
              to="../devices"
              replace
            />
          }
        />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Route>
    </Routes>
  );
}
