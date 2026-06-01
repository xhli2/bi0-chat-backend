from app.agent.skill_resolver import resolve_runtime_tool_names, resolve_skills


def test_gene_annotation_skill_resolves_tools():
    skills = resolve_skills(prompt="BRCA1 gene annotation mygene ensembl", agent_type="research")
    names = [skill.name for skill in skills]
    assert "gene-annotation" in names
    tools = resolve_runtime_tool_names(agent_type="research", skill_names=names)
    assert "bio_mygene_query" in tools
    assert "bio_ensembl_gene_lookup" in tools


def test_variant_consequence_skill():
    skills = resolve_skills(prompt="annotate variant with ensembl vep HGVS", agent_type="research")
    names = [skill.name for skill in skills]
    assert "variant-consequence" in names
    tools = resolve_runtime_tool_names(agent_type="research", skill_names=names)
    assert "bio_ensembl_vep" in tools


def test_structure_lookup_skill():
    skills = resolve_skills(prompt="find pdb structure and alphafold model", agent_type="research")
    names = [skill.name for skill in skills]
    assert "structure-lookup" in names
    tools = resolve_runtime_tool_names(agent_type="research", skill_names=names)
    assert "bio_pdb_search" in tools
    assert "bio_alphafold_lookup" in tools


def test_clinvar_skill_uses_ncbi():
    skills = resolve_skills(prompt="clinvar pathogenic clinical significance", agent_type="research")
    names = [skill.name for skill in skills]
    assert "clinvar-lookup" in names
    tools = resolve_runtime_tool_names(agent_type="research", skill_names=names)
    assert "bio_ncbi_search" in tools


def test_sequence_utils_skill():
    skills = resolve_skills(prompt="compute gc content from fasta sequence", agent_type="research")
    names = [skill.name for skill in skills]
    assert "sequence-utils" in names
    tools = resolve_runtime_tool_names(agent_type="research", skill_names=names)
    assert "bio_script_runner" in tools
