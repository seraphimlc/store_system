/** Keep a person unless every staff/leader account for this code is soft-deleted. Persons with no account stay. */
export function excludeDeletedStaff(personColumn: string) {
  return `NOT (EXISTS (SELECT 1 FROM users deleted_account WHERE deleted_account.person_code=${personColumn} AND deleted_account.role IN ('staff','leader') AND deleted_account.status='deleted') AND NOT EXISTS (SELECT 1 FROM users live_account WHERE live_account.person_code=${personColumn} AND live_account.role IN ('staff','leader') AND live_account.status<>'deleted'))`;
}
