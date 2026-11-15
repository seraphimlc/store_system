/** Keep a person unless every staff/leader account for this code is soft-deleted. Persons with no account stay. */
export function excludeDeletedStaff(personColumn: string) {
  return `NOT (EXISTS (SELECT 1 FROM users deleted_account WHERE deleted_account.person_code=${personColumn} AND deleted_account.role IN ('staff','leader') AND deleted_account.status='deleted') AND NOT EXISTS (SELECT 1 FROM users live_account WHERE live_account.person_code=${personColumn} AND live_account.role IN ('staff','leader') AND live_account.status<>'deleted'))`;
}

/** Hide departed/deleted staff from options without changing historical record visibility or accountless people. */
export function selectableStaff(personColumn: string) {
  return `NOT (EXISTS (SELECT 1 FROM users departed_account WHERE departed_account.person_code=${personColumn} AND departed_account.role IN ('staff','leader') AND departed_account.status IN ('resigned','deleted')) AND NOT EXISTS (SELECT 1 FROM users employed_account WHERE employed_account.person_code=${personColumn} AND employed_account.role IN ('staff','leader') AND employed_account.status NOT IN ('resigned','deleted')))`;
}
