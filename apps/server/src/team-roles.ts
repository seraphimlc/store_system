import type {PoolClient} from 'pg';

export async function syncTeamAccountRoles(client: PoolClient) {
  return (await client.query(`WITH desired AS(
    SELECT u.id, CASE WHEN EXISTS(
      SELECT 1 FROM bd_team_member m JOIN bd_team t ON t.id=m.team_id
      WHERE m.person_code=u.person_code AND m.end_date IS NULL AND m.role='leader' AND t.status='active'
    ) THEN 'leader' ELSE 'staff' END AS role
    FROM users u WHERE u.role IN ('staff','leader') AND u.person_code IS NOT NULL
  ) UPDATE users u SET role=d.role FROM desired d WHERE u.id=d.id AND u.role<>d.role
  RETURNING u.person_code AS code, u.role`)).rows as {code: string; role: string}[];
}
