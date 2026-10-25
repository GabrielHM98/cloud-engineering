PRAGMA foreign_keys = ON;

CREATE TABLE catalog_metadata (
    key TEXT PRIMARY KEY NOT NULL,
    value_json TEXT NOT NULL
);

CREATE TABLE molecule (
    molecule_id TEXT PRIMARY KEY NOT NULL,
    canonical_smiles TEXT NOT NULL UNIQUE,
    computed_inchikey TEXT NOT NULL,
    heavy_atoms INTEGER NOT NULL CHECK (heavy_atoms > 0),
    formal_charge INTEGER NOT NULL
);

CREATE TABLE substance (
    dtxsid TEXT PRIMARY KEY NOT NULL,
    returned_dtxsid TEXT NOT NULL,
    molecule_id TEXT REFERENCES molecule(molecule_id),
    preferred_name TEXT NOT NULL,
    casrn TEXT NOT NULL,
    dtxcid TEXT NOT NULL,
    validation_status TEXT NOT NULL,
    original_smiles TEXT NOT NULL,
    source_inchikey TEXT NOT NULL,
    identifier_check TEXT NOT NULL,
    qc_level TEXT NOT NULL,
    qc_notes TEXT NOT NULL,
    source_csv TEXT NOT NULL,
    source_csv_sha256 TEXT NOT NULL,
    csv_record_number TEXT NOT NULL,
    export_record_count INTEGER NOT NULL CHECK (export_record_count >= 0),
    UNIQUE(dtxsid, molecule_id),
    CHECK ((validation_status = 'accepted' AND molecule_id IS NOT NULL)
        OR (validation_status <> 'accepted' AND molecule_id IS NULL))
);

CREATE TABLE function_category (
    function_id TEXT PRIMARY KEY NOT NULL,
    name TEXT NOT NULL UNIQUE,
    definition TEXT NOT NULL,
    vocabulary_kind TEXT NOT NULL
);

CREATE TABLE recorded_use (
    molecule_id TEXT NOT NULL REFERENCES molecule(molecule_id),
    function_id TEXT NOT NULL REFERENCES function_category(function_id),
    evidence_records INTEGER NOT NULL CHECK (evidence_records > 0),
    distinct_documents INTEGER NOT NULL CHECK (distinct_documents >= 0),
    PRIMARY KEY (molecule_id, function_id)
);

CREATE TABLE use_evidence (
    evidence_id INTEGER PRIMARY KEY,
    molecule_id TEXT NOT NULL,
    function_id TEXT NOT NULL,
    dtxsid TEXT NOT NULL,
    source_name TEXT NOT NULL,
    source_url TEXT NOT NULL,
    document_id TEXT NOT NULL,
    document_url TEXT NOT NULL,
    raw_functional_use TEXT NOT NULL,
    FOREIGN KEY (molecule_id, function_id) REFERENCES recorded_use(molecule_id, function_id),
    FOREIGN KEY (dtxsid, molecule_id) REFERENCES substance(dtxsid, molecule_id)
);

CREATE INDEX substance_molecule_idx ON substance(molecule_id);
CREATE INDEX substance_name_idx ON substance(preferred_name COLLATE NOCASE);
CREATE INDEX substance_casrn_idx ON substance(casrn);
CREATE INDEX recorded_use_function_idx ON recorded_use(function_id, molecule_id);
CREATE INDEX evidence_pair_idx ON use_evidence(molecule_id, function_id);
CREATE INDEX evidence_substance_idx ON use_evidence(dtxsid);

CREATE VIEW function_support AS
SELECT f.function_id, f.name, f.definition,
       count(r.molecule_id) AS accepted_molecules,
       coalesce(sum(r.evidence_records), 0) AS supporting_records
FROM function_category f
LEFT JOIN recorded_use r ON r.function_id = f.function_id
GROUP BY f.function_id, f.name, f.definition;
