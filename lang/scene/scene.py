import time, sys, logging, bpy, os
from bpy.types import Scene, Object, Material
from bpy.props import IntVectorProperty
from ...types import BFNamelist, FDSCase, BFException, BFNotImported
from ... import utils
from ..object.MOVE import ON_MOVE

log = logging.getLogger(__name__)

def _import(scene, context, fds_case, fds_label=None):
    """!
    Import all namelists with label fds_label from fds_case into scene.
    """
    while True:
        fds_namelist = fds_case.get_fds_namelist(fds_label=fds_label, remove=True)
        if not fds_namelist:
            break
        _import_fds_namelist(scene, context, fds_namelist)


def _import_fds_namelist(scene, context, fds_namelist):
    """!
    Import a fds_namelist from fds_case into scene.
    """
    is_imported = False
    fds_label = fds_namelist.fds_label
    bf_namelist = BFNamelist.get_subclass(fds_label=fds_label)
    if bf_namelist:
        hid = f"Imported {fds_label}"
        match bf_namelist.bpy_type:
            case t if t == Object:
                me = bpy.data.meshes.new(hid)  # new Mesh
                ob = bpy.data.objects.new(hid, object_data=me)  # new Object
                scene.collection.objects.link(ob)  # link it to Scene Collection
                try:
                    ob.from_fds(context, fds_namelist=fds_namelist)
                except BFNotImported:
                    bpy.data.objects.remove(ob, do_unlink=True)
                else:
                    is_imported = True
            case t if t == Scene:
                try:
                    bf_namelist(element=scene).from_fds(context, fds_namelist=fds_namelist)
                except BFNotImported:
                    pass
                else:
                    is_imported = True
            case t if t == Material:
                ma = bpy.data.materials.new(hid)  # new Material
                try:
                    ma.from_fds(context, fds_namelist=fds_namelist)
                except BFNotImported:
                    bpy.data.materials.remove(ma, do_unlink=True)
                else:
                    ma.use_fake_user = True  # prevent del (eg. used by PART)
                    is_imported = True
            case _:
                raise AssertionError(f"Unhandled bf_namelist for <{fds_namelist}>") 
    if not is_imported:  # last resort, import to Free Text
        scene.bf_config_text.write(fds_namelist.to_fds(context) + "\n")

def _get_id_to_fds_namelist_dict(context, fds_case, fds_label):
    """!
    Return all fds_namelists with fds_label into a {ID: fds_namelist} dict.
    """
    id_to_fds_namelist = dict()
    while True:
        fds_namelist = fds_case.get_fds_namelist(fds_label=fds_label, remove=True)
        if not fds_namelist:
            break
        p_id = fds_namelist.get_fds_param(fds_label="ID", remove=False)
        if not p_id:
            raise BFNotImported(None, "Missing ID: <{fds_namelist}>")
        id_to_fds_namelist[p_id.get_value(context)] = fds_namelist  # .copy()  # FIXME because it gets consumed
    return id_to_fds_namelist

class BFScene:
    """!
    Extension of Blender Scene.
    """

    @property
    def bf_namelists(self):
        """!
        Return related bf_namelist, instance of BFNamelist.
        """
        return (n(element=self) for n in BFNamelist.subclasses if n.bpy_type == Scene)

    def to_fds(self, context, full=False, all_surfs=False, save=False):
        """!
        Return the FDS formatted string.
        @param context: the Blender context.
        @param full: if True, return full FDS case.
        @param all_surfs: if True, return all SURF namelists, even if not related.
        @param save: if True, save to disk.
        @return FDS formatted string (eg. "&OBST ID='Test' /"), or None.
        """
        lines = list()

        # Set mysef as the right Scene instance in the context
        # It is needed, because context.scene is needed elsewhere
        bpy.context.window.scene = self  # set context.scene

        # Header
        if full:
            v = sys.modules["blenderfds"].bl_info["version"]
            blv = bpy.app.version_string
            now = time.strftime("%a, %d %b %Y, %H:%M:%S", time.localtime())
            blend_filepath = bpy.data.filepath or "not saved"
            if len(blend_filepath) > 60:
                blend_filepath = "..." + blend_filepath[-57:]
            lines.extend(  # header has !!!
                (
                    f"!!! Generated by BlenderFDS {v[0]}.{v[1]}.{v[2]} on Blender {blv}",
                    f"!!! Date: <{now}>",
                    f"!!! File: <{blend_filepath}>",
                    f"! --- Case from Blender Scene <{self.name}> and View Layer <{context.view_layer.name}>",
                )
            )

        # Append Scene namelists
        lines.extend(
            bf_namelist.to_fds(context)
            for bf_namelist in self.bf_namelists
            if bf_namelist
        )

        # Append free text
        if self.bf_config_text:
            text = self.bf_config_text.as_string()
            if text:
                text = f"\n! --- Free text from Blender Text <{self.bf_config_text.name}>\n{text}"
                lines.append(text)

        # Append Material namelists
        if full:
            if all_surfs:
                header = "\n! --- Boundary conditions from all Blender Materials"
                mas = list(ma for ma in bpy.data.materials)  # all
            else:
                header = "\n! --- Boundary conditions from Blender Materials"
                mas = list(  # related to scene
                    set(
                        ms.material
                        for ob in self.objects
                        for ms in ob.material_slots
                        if ms.material
                    )
                )
            mas.sort(key=lambda k: k.name)  # alphabetic sorting by name
            ma_lines = list(ma.to_fds(context) for ma in mas)
            if any(ma_lines):
                lines.append(header)
                lines.extend(ma_lines)

        # Append Collections and their Objects
        if full:
            text = self.collection.to_fds(context)
            if text:
                lines.append("\n! --- Geometric namelists from Blender Collections")
                lines.append(text)

        # Append TAIL
        if full and self.bf_head_export:
            lines.append("\n&TAIL /\n ")

        # Write and return
        fds_text = "\n".join(l for l in lines if l)
        if save:
            filepath = utils.io.transform_rbl_to_abs(
                filepath_rbl=self.bf_config_directory,
                name=self.name,
                extension=".fds"
            )
            utils.io.write_txt_file(filepath, fds_text)
        return fds_text

    def from_fds(self, context, filepath=None, f90=None):
        """!
        Set self.bf_namelists from FDSCase, on error raise BFException.
        @param context: the Blender context.
        @param filepath: filepath of FDS case to be imported.
        @param f90: FDS formatted string of namelists, eg. "&OBST ID='Test' /\n&TAIL /".
        """
        # Set mysef as the right Scene instance in the context
        # this is used by context.scene calls elsewhere
        bpy.context.window.scene = self

        # Init
        filepath = utils.io.transform_rbl_to_abs(filepath)
        fds_case = FDSCase()
        fds_case.from_fds(filepath=filepath, f90=f90)

        # Set imported fds case dir, because others rely on it
        # it is removed later
        self.bf_config_directory = os.path.dirname(filepath)

        # Prepare free text for unmanaged namelists
        free_text = bpy.data.texts.new(f"Imported text")
        self.bf_config_text = free_text

        # Import SURFs first to new materials
        _import(fds_case=fds_case, fds_label="SURF", scene=self, context=context)

        # Get all MOVEs into an id to fds_namelist dict
        move_id_to_move = _get_id_to_fds_namelist_dict(context=context, fds_case=fds_case, fds_label="MOVE")

        # Import OBSTs before VENTs
        _import(fds_case=fds_case, fds_label="OBST", scene=self, context=context)

        # Import all other namelists to Object or Scene
        _import(fds_case=fds_case, fds_label=None, scene=self, context=context)

        # Transform the Objects that have a MOVE_ID
        for ob in self.collection.objects:
            move_id = ob.get("MOVE_ID")  # tmp property
            if not move_id:
                continue
            del ob["MOVE_ID"]  # clean up of tmp property
            # Get the called MOVE
            fds_namelist = move_id_to_move.get(move_id)
            if not fds_namelist:
                raise BFException(self, f"Missing MOVE ID='{ob.bf_move_id}'")  # FIXME compatible err msgs
            # Apply the called MOVE to the Object
            ON_MOVE(ob).from_fds(
                context=context,
                fds_namelist=fds_namelist.copy()  # it is consumed
            )

        # Set imported Scene visibility
        context.window.scene = self

        # Show free text
        free_text.current_line_index = 0
        bpy.ops.scene.bf_show_text()  # FIXME FIXME FIXME remove ops, put py

        # Disconnect from fds case dir, to avoid overwriting imported case
        self.bf_config_directory = ""


    @classmethod
    def register(cls):
        """!
        Register related Blender properties.
        @param cls: class to be registered.
        """
        Scene.bf_namelists = cls.bf_namelists
        Scene.to_fds = cls.to_fds
        Scene.from_fds = cls.from_fds
        Scene.bf_file_version = IntVectorProperty(
            name="BlenderFDS File Version", size=3
        )

    @classmethod
    def unregister(cls):
        """!
        Unregister related Blender properties.
        @param cls: class to be unregistered.
        """
        del Scene.bf_file_version
        del Scene.from_fds
        del Scene.to_fds
        del Scene.bf_namelists
