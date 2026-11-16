import { describe, expect, test } from "bun:test";

import {
  getAuthenticatedSessionIdentity,
  getOrganizationScope,
} from "../lib/auth/sessionScope.ts";

describe("getAuthenticatedSessionIdentity", () => {
  test("keeps valid user ids in distinct typed cache partitions", () => {
    expect(getAuthenticatedSessionIdentity(42, {})).toBe("authenticated:number:42");
    expect(getAuthenticatedSessionIdentity("42", {})).toBe("authenticated:string:42");
  });

  test("uses a stable opaque partition for an id-less session object", () => {
    const firstSession = {};
    const secondSession = {};

    const firstIdentity = getAuthenticatedSessionIdentity(undefined, firstSession);
    expect(getAuthenticatedSessionIdentity(null, firstSession)).toBe(firstIdentity);
    expect(getAuthenticatedSessionIdentity(undefined, secondSession)).not.toBe(firstIdentity);
  });
});

describe("getOrganizationScope", () => {
  test("uses the matched tenant route when the browser URL was rewritten", () => {
    expect(getOrganizationScope("/course/abc", { orgslug: "school-a" })).toBe("school-a");
  });

  test("reads an organization from an explicit org route when params are absent", () => {
    expect(getOrganizationScope("/orgs/school-b/course/abc")).toBe("school-b");
  });

  test("reads an organization from an embed route", () => {
    expect(getOrganizationScope("/embed/school-c/activity/abc")).toBe("school-c");
  });

  test("does not treat malformed tenant values as a cache scope", () => {
    expect(getOrganizationScope("/course/abc", { orgslug: "https://other.test" })).toBe("global");
    expect(getOrganizationScope("/orgs/../course/abc")).toBe("global");
  });

  test("uses a stable global partition when there is no tenant route", () => {
    expect(getOrganizationScope("/account/general", {})).toBe("global");
  });
});
