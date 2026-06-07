fn main() {
    protobuf_codegen::Codegen::new()
        .pure()
        .includes(&["../protos"])
        .input("../protos/todolist.proto")
        .cargo_out_dir("protos")
        .run_from_script();
}
